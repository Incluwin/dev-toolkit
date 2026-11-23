"""A model plays the app toward a goal, from what is on screen.

Each step: capture -> text regions (OCR) -> a bounded table of candidate actions -> one typed Choice
over that table (TypeSafe Jev) -> the chosen action as ordinary input -> look again.

The model never sees pixels or produces coordinates: it can only pick a row of the table, and an
answer outside the table is refused. Below `min_confidence` the loop stops and reports instead of
clicking, so a guess never becomes input. Evidence per step (capture + decision) lands in the run
folder as step<NN>.png and playtest.jsonl.

An app improves on the default table by passing its own `build_candidates` (e.g. "double-click the
character above this name tag") and `instructions`. Lessons from doing that on a game:
  * keep the table small (about 16 click targets): option count drags Choice confidence down
  * leave out long texts (prose, speech balloons): they are not things to click
  * tell the model what does NOT count as progress (ambient text that looks like a reply)
  * filter targets by something measurable (a name tag's dark background), not by text alone

Needs `pip install typesafe-sdk` and TYPESAFE_API_KEY in the environment. Roughly 9 s and 3k tokens
per step with a cold perception worker.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Callable

from .perception import region_center
from .window import AppWindow

MIN_REGION_CONFIDENCE = 0.6
MAX_CLICK_TARGETS = 16
MAX_CLICK_WORDS = 4
MIN_DECISION_CONFIDENCE = 0.35
SCREEN_TEXT_LIMIT = 80
SETTLE_S = 1.5
INSTRUCTIONS = (
    "You are operating an application through its screen, as a person would. `goal` is what the user wants; "
    "`screen.texts` is everything readable on screen now (OCR, may have typos); `history` is what was already "
    "tried. Pick the one action that best advances the goal from this screen. Choose done only if the screen "
    "shows the goal is complete, wait if the app is loading or a dialog is still appearing, and give_up if no "
    "listed action can advance the goal.")


@dataclass
class Candidate:
    id: str
    description: str
    kind: str          # click | dclick | wait | done | give_up
    x: int = 0
    y: int = 0


def stop_candidates() -> list[Candidate]:
    return [Candidate("wait", "wait a moment and look again (loading, animation, dialog appearing)", "wait"),
            Candidate("done", "stop: the screen shows the goal is complete", "done"),
            Candidate("give_up", "stop: no listed action can advance the goal", "give_up")]


def click_candidates(regions: list[dict], max_targets: int = MAX_CLICK_TARGETS,
                     max_words: int = MAX_CLICK_WORDS) -> list[Candidate]:
    """One click per distinct short text, most confident first, listed in reading order."""
    texts, seen = [], set()
    for r in sorted(regions, key=lambda r: -r["confidence"]):
        if r["kind"] == "text" and r["text"] not in seen and len(r["text"].split()) <= max_words:
            seen.add(r["text"])
            texts.append(r)
    texts = sorted(texts[:max_targets], key=lambda r: (region_center(r)[1], region_center(r)[0]))
    return [Candidate(f"click-{i}", f'click the text "{r["text"]}" at {region_center(r)}', "click", *region_center(r))
            for i, r in enumerate(texts)]


def default_candidates(regions: list[dict], window: AppWindow) -> list[Candidate]:
    return click_candidates(regions) + stop_candidates()


def screen_state(regions: list[dict], size: tuple[int, int]) -> dict:
    texts = sorted((r for r in regions if r["kind"] == "text" and r["confidence"] >= MIN_REGION_CONFIDENCE),
                   key=lambda r: (region_center(r)[1] // 12, region_center(r)[0]))[:SCREEN_TEXT_LIMIT]
    return {"size": list(size), "texts": [{"text": r["text"], "at": list(region_center(r))} for r in texts]}


def choose(client, goal: str, screen: dict, table: list[Candidate], history: list[dict], instructions: str,
           extra_state: dict | None = None):
    from typesafe_sdk import Choice
    criteria = {c.id: c.description for c in table}
    state = {"goal": goal, "screen": screen, "history": history, **(extra_state or {})}
    response = client.system_one(state=state, questions={"action": Choice(instructions=instructions, criteria=criteria)})
    answer = response.choices["action"]
    if answer.choice not in criteria:
        raise RuntimeError(f"the model answered {answer.choice!r}, which is not in the candidate table")
    chosen = next(c for c in table if c.id == answer.choice)
    top = sorted(answer.probabilities.items(), key=lambda kv: -kv[1])[:3]
    return chosen, answer.confidence, top, response.usage


async def play(window: AppWindow, goal: str, steps: int = 12, dry_run: bool = False,
               build_candidates: Callable[[list[dict], AppWindow], list[Candidate]] = default_candidates,
               instructions: str = INSTRUCTIONS, extra_state: Callable[[list[dict]], dict] | None = None,
               min_confidence: float = MIN_DECISION_CONFIDENCE) -> int:
    """0 when the model reports the goal done (or after a dry run), 1 otherwise. dry_run observes and
    decides once without sending any input (safe while someone else is using the app)."""
    from typesafe_sdk import TypeSafeClient
    evidence = window.config.run_dir()
    evidence.mkdir(parents=True, exist_ok=True)
    history: list[dict] = []
    with TypeSafeClient() as client, open(evidence / "playtest.jsonl", "a", encoding="utf-8") as log:
        for step in range(1, steps + 1):
            started = time.time()
            await window.refresh()
            regions = await window.regions(min_confidence=MIN_REGION_CONFIDENCE, out=evidence / f"step{step:02d}.png")
            table = build_candidates(regions, window)
            screen = screen_state(regions, (window.bounds["width"], window.bounds["height"]))
            chosen, confidence, top, usage = choose(client, goal, screen, table, history, instructions,
                                                    extra_state(regions) if extra_state else None)
            record = {"step": step, "action": chosen.description, "kind": chosen.kind,
                      "confidence": round(confidence, 3), "top": [[k, round(v, 3)] for k, v in top],
                      "options": len(table), "usage": str(usage), "seconds": round(time.time() - started, 1)}
            log.write(json.dumps(record) + "\n")
            print(f"step {step}: {chosen.description}  (confidence {confidence:.2f}, {len(table)} options, "
                  f"{record['seconds']} s)", flush=True)
            if dry_run or chosen.kind in ("done", "give_up"):
                print(f"{'dry run' if dry_run else chosen.kind}; evidence in {evidence}")
                return 0 if dry_run or chosen.kind == "done" else 1
            if confidence < min_confidence:
                options = {c.id: c.description for c in table}
                print("unsure, stopping; top options: " + "; ".join(f"{options[k]} ({v:.2f})" for k, v in top))
                print(f"evidence in {evidence}")
                return 1
            if chosen.kind == "click":
                await window.click(chosen.x, chosen.y)
            elif chosen.kind == "dclick":
                await window.double_click(chosen.x, chosen.y)
            history.append({"step": step, "action": chosen.description})
            await asyncio.sleep(SETTLE_S)
    print(f"step budget ({steps}) spent without done; evidence in {evidence}")
    return 1
