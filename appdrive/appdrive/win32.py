"""Win32 pieces cua-driver does not cover: real key holds (SendInput), the foreground lock, responsiveness.

Games read movement and hotkeys from real key state (DirectInput / GetAsyncKeyState), which a posted
key message never reaches, so a hold has to go through SendInput while the target window has focus.
"""

from __future__ import annotations

import asyncio
import ctypes
import time
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.GetForegroundWindow.restype = ctypes.c_size_t
user32.SetForegroundWindow.argtypes = [ctypes.c_size_t]
user32.SendMessageTimeoutW.argtypes = [ctypes.c_size_t, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t,
                                       wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
SMTO_ABORTIFHUNG = 0x2
RESPONSIVE_CHECKS = 3

KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP = 0x1, 0x2
VK = {"left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28, "alt": 0x12, "ctrl": 0x11,
      "shift": 0x10, "space": 0x20, "insert": 0x2D, "delete": 0x2E, "home": 0x24, "end": 0x23,
      "pageup": 0x21, "pagedown": 0x22, "return": 0x0D, "escape": 0x1B, "tab": 0x09,
      **{f"f{n}": 0x6F + n for n in range(1, 13)}}
EXTENDED = {"left", "up", "right", "down", "insert", "delete", "home", "end", "pageup", "pagedown"}


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


def send_key(name: str, up: bool) -> None:
    vk = VK.get(name) or (ord(name.upper()) if len(name) == 1 and name.isalnum() else None)
    if vk is None:
        raise ValueError(f"unknown hold key {name!r}")
    flags = (KEYEVENTF_EXTENDEDKEY if name in EXTENDED else 0) | (KEYEVENTF_KEYUP if up else 0)
    event = INPUT(type=1)
    event.ki = KEYBDINPUT(vk, user32.MapVirtualKeyW(vk, 0), flags, 0, 0)
    if user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(INPUT)) != 1:
        raise ctypes.WinError(ctypes.get_last_error())


def foreground_window() -> int:
    return user32.GetForegroundWindow()


def force_foreground(hwnd: int) -> bool:
    """Windows' foreground lock refuses a switch unless this process sent the last input: an Alt tap lifts it."""
    send_key("alt", up=False)
    send_key("alt", up=True)
    user32.SetForegroundWindow(hwnd)
    return user32.GetForegroundWindow() == hwnd


def set_cursor_pos(x: int, y: int) -> None:
    user32.SetCursorPos(int(x), int(y))


async def wait_responsive(hwnd: int, timeout_s: float = 90) -> None:
    """An app that stops pumping messages while it loads gets a "(Not Responding)" ghost window if input
    arrives then. Wait until it answers WM_NULL several times in a row."""
    ok, deadline = 0, time.monotonic() + timeout_s
    while ok < RESPONSIVE_CHECKS:
        if time.monotonic() > deadline:
            raise RuntimeError(f"window still not responding after {timeout_s:.0f} s")
        reply = ctypes.c_size_t()
        answered = user32.SendMessageTimeoutW(hwnd, 0, 0, 0, SMTO_ABORTIFHUNG, 1000, ctypes.byref(reply))
        ok = ok + 1 if answered else 0
        await asyncio.sleep(0.5)


async def hold_keys(hwnd: int, keys: list[str], ms: int) -> None:
    """Hold real keys for ms. The caller focuses the window first; the keys are released early (and the
    hold fails) if the user focuses another window, so a hold never types into someone else's app."""
    for k in keys:
        send_key(k, up=False)
    try:
        deadline = time.monotonic() + ms / 1000
        while time.monotonic() < deadline:
            if foreground_window() != hwnd:
                raise RuntimeError("window lost focus during the hold; released keys")
            await asyncio.sleep(0.02)
    finally:
        for k in reversed(keys):
            send_key(k, up=True)
