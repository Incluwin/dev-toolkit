"""Drive one application window through cua-driver, in-process, under a capability manifest.

The manifest is a deny-by-default ceiling: the driver can only see and act on the apps it lists, so a
script (or an agent running it) cannot touch anything else on the desktop. See
capabilities.example.yaml.

What a typical game client accepts (measured on a DirectX game with no UIA provider):
  * window capture works; skip the accessibility walk (no UIA tree, and the walk times out)
  * mouse clicks land only with delivery_mode="foreground"
  * posted keys and typed text land only while the app is the foreground window
  * held keys need SendInput (win32.hold_keys); cua-driver has no key hold on Windows
Ordinary desktop apps accept more (background clicks, UIA element indices); set
click_delivery="background" for those.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

import cua_driver as cua
from PIL import Image

from . import perception, win32

# Tools whose schema accepts a session label; the rest reject unknown fields.
SESSION_TOOLS = {"get_window_state", "click", "drag", "press_key", "type_text", "set_window_frame"}


@dataclass
class AppConfig:
    manifest: Path                       # cua-driver capability manifest (yaml)
    match: Callable[[dict], bool]        # picks the window out of list_windows rows (app_name, title, pid, ...)
    evidence: Path = Path("evidence")    # captures land in evidence/<timestamp>/
    session: str = "appdrive"
    click_delivery: str = "foreground"   # "background" for apps that take posted clicks
    not_found: str = "no matching window; is the app running?"
    extra: dict = field(default_factory=dict)

    def run_dir(self) -> Path:
        return self.evidence / datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")


def window_matcher(app_name: str | None = None, title: str | None = None, title_prefix: str | None = None,
                   pid: int | None = None) -> Callable[[dict], bool]:
    def match(w: dict) -> bool:
        return ((app_name is None or w["app_name"].lower() == app_name.lower())
                and (title is None or w["title"] == title)
                and (title_prefix is None or w["title"].startswith(title_prefix))
                and (pid is None or w["pid"] == pid))
    return match


def create_driver(manifest: Path) -> cua.CuaDriver:
    if not Path(manifest).is_file():
        raise RuntimeError(f"no capability manifest at {manifest} (copy the .example.yaml and set your paths)")
    mode = cua.SessionPermissionMode.STANDARD
    auth = cua.RuntimeAuthorizationOptions(
        allowed_modes=[mode], compatibility_mode=mode,
        compatibility_capability_manifest_path=str(manifest),
        compatibility_bounded_manifest_path=None, unrestricted_acknowledged=False,
        max_session_ttl_seconds=3600, max_idle_ttl_seconds=600)
    return cua.CuaDriver.create_configured(
        cua.ConfiguredDriverOptions(claude_code_compatibility=False, authorization=auth))


async def call_tool(driver: cua.CuaDriver, tool: str, session: str, **args) -> dict:
    if tool in SESSION_TOOLS:
        args["session"] = session
    result = await driver.call_tool(tool, json.dumps(args))
    payload = json.loads(result.structured_json) if result.structured_json else {"text": result.text}
    if result.is_error:
        raise RuntimeError(f"{tool} failed: {payload}")
    return payload


class AppWindow:
    """One window. Coordinates are pixels of a fresh capture of that window."""

    def __init__(self, driver: cua.CuaDriver, config: AppConfig, pid: int, window_id: int, bounds: dict):
        self.driver, self.config = driver, config
        self.pid, self.window_id, self.bounds = pid, window_id, bounds
        self.last_shot: Path | None = None   # the newest capture saved by shot()/regions()

    @property
    def target(self) -> dict:
        return {"kind": "window", "pid": self.pid, "window_id": self.window_id}

    async def call(self, tool: str, **args) -> dict:
        return await call_tool(self.driver, tool, self.config.session, **args)

    async def refresh(self) -> None:
        windows = (await self.call("list_windows", pid=self.pid))["windows"]
        self.bounds = next((w for w in windows if w["window_id"] == self.window_id), windows[0])["bounds"]

    def focused(self) -> bool:
        return win32.foreground_window() == self.window_id

    async def wait_responsive(self, timeout_s: float = 90) -> None:
        await win32.wait_responsive(self.window_id, timeout_s)

    async def focus(self) -> None:
        if self.focused():
            return
        result = await self.call("bring_to_front", pid=self.pid, window_id=self.window_id)
        if not self.focused() and not win32.force_foreground(self.window_id):
            raise RuntimeError(f"Windows refused to focus the window: {result}")

    async def _ground(self) -> None:
        # Pixel input is grounded on the latest capture of this window, so capture first.
        await self.call("get_window_state", pid=self.pid, window_id=self.window_id, include_accessibility_tree=False)

    async def shot(self, out: Path) -> Path:
        out.parent.mkdir(parents=True, exist_ok=True)
        await self.call("get_window_state", pid=self.pid, window_id=self.window_id,
                        include_accessibility_tree=False, screenshot_out_file=str(out))
        self.last_shot = out
        return out

    async def capture(self) -> Image.Image:
        result = await self.driver.call_tool("get_window_state", json.dumps({
            "session": self.config.session, "pid": self.pid, "window_id": self.window_id,
            "include_accessibility_tree": False}))
        if result.is_error or not result.images:
            raise RuntimeError(f"capture failed: {result.structured_json or result.text}")
        return Image.open(io.BytesIO(base64.b64decode(result.images[0].data_base64))).convert("RGB")

    async def regions(self, kinds: list[str] | None = None, min_confidence: float = 0.5,
                      out: Path | None = None) -> list[dict]:
        """Text/icon regions of a fresh capture (cua-perception), in capture pixels."""
        png = await self.shot(out or self.config.run_dir() / "regions.png")
        return [r for r in perception.perceive(png, self.target)
                if r["confidence"] >= min_confidence and (not kinds or r["kind"] in kinds)]

    async def find_text(self, text: str, min_confidence: float = 0.5) -> list[dict]:
        return perception.match_text(await self.regions(["text"], min_confidence), text)

    async def click(self, x: float, y: float, count: int = 1) -> None:
        if self.config.click_delivery == "foreground":
            await self.focus()
        await self._ground()
        args = {"count": count} if count != 1 else {}
        await self.call("click", target=self.target, x=x, y=y, delivery_mode=self.config.click_delivery, **args)

    async def double_click(self, x: float, y: float) -> None:
        await self.click(x, y, count=2)

    async def click_text(self, text: str, dx: int = 0, dy: int = 0, double: bool = False) -> tuple[dict, int, int]:
        """Click the one text region matching `text` (offset by dx, dy: e.g. a sprite above its label)."""
        region = perception.only_match(await self.find_text(text), text)
        x, y = perception.region_center(region)
        x, y = x + dx, y + dy
        await self.click(x, y, count=2 if double else 1)
        return region, x, y

    async def drag(self, x1: float, y1: float, x2: float, y2: float, duration_ms: int = 700) -> None:
        await self._ground()
        await self.call("drag", pid=self.pid, window_id=self.window_id, from_x=x1, from_y=y1, to_x=x2, to_y=y2,
                        duration_ms=duration_ms, steps=30, delivery_mode=self.config.click_delivery)

    async def key(self, key: str) -> None:
        await self.focus()
        await self.call("press_key", target=self.target, key=key)

    async def type(self, text: str) -> None:
        await self.focus()
        await self.call("type_text", target=self.target, text=text)

    async def hold(self, keys: list[str], ms: int) -> None:
        """Real key hold (SendInput), released early if the window loses focus."""
        await self.focus()
        await win32.hold_keys(self.window_id, keys, ms)

    async def pin(self, x: int = 0, y: int = 0) -> None:
        """Move the window to a fixed spot so recordings and absolute pixels stay stable."""
        await self.call("set_window_frame", pid=self.pid, window_id=self.window_id, x=x, y=y,
                        width=self.bounds["width"], height=self.bounds["height"])

    def park_cursor(self, inset: int = 12) -> None:
        """Rest the mouse in the window's bottom-right corner (games draw their own cursor where it sits)."""
        win32.set_cursor_pos(self.bounds["x"] + self.bounds["width"] - inset,
                             self.bounds["y"] + self.bounds["height"] - inset)


async def list_windows(driver: cua.CuaDriver, config: AppConfig) -> list[dict]:
    return (await call_tool(driver, "list_windows", config.session))["windows"]


async def open_window(driver: cua.CuaDriver, config: AppConfig, cls: type[AppWindow] = AppWindow) -> AppWindow:
    found = [w for w in await list_windows(driver, config) if config.match(w)]
    if not found:
        raise RuntimeError(config.not_found)
    w = found[0]
    return cls(driver, config, w["pid"], w["window_id"], w["bounds"])


async def poll(probe: Callable[[], bool], timeout_s: float, failure: Callable[[], str], every_s: float = 0.5) -> None:
    """Wait until probe() holds; RuntimeError(failure()) when the time runs out."""
    import time
    deadline = time.monotonic() + timeout_s
    while not probe():
        if time.monotonic() > deadline:
            raise RuntimeError(failure())
        await asyncio.sleep(every_s)
