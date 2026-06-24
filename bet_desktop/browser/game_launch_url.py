"""Capture and verify the real baccarat game launch URL.

The browser address bar can point to a platform wrapper. The usable game URL is
often the document URL loaded by the game frame, and it carries sensitive launch
parameters. This module keeps the full URL in memory only and exposes redacted
summaries for logs/UI.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlsplit


_STATIC_ASSET_RE = re.compile(r"\.(png|jpe?g|webp|gif|css|js|mp3|mp4|woff2?|ttf|wasm)(\?|$)", re.I)
_SENSITIVE_QUERY_RE = re.compile(r"(token|account|extra|auth|session|cookie|key|sign|credential)", re.I)
HALL_ENTRY_BASE_WIDTH = 960
HALL_ENTRY_BASE_HEIGHT = 620


LAUNCH_URLS_JS = r"""
() => {
  const urls = [];
  try { urls.push(location.href); } catch (_) {}
  try { urls.push(...performance.getEntriesByType("navigation").map((entry) => entry.name)); } catch (_) {}
  try { urls.push(...performance.getEntriesByType("resource").map((entry) => entry.name)); } catch (_) {}
  return Array.from(new Set(urls)).slice(-700);
}
"""


BACCARAT_LOAD_STATE_JS = r"""
() => {
  function countRooms(value) {
    if (!value || typeof value !== "object") return 0;
    const counts = [];
    try { if (Array.isArray(value)) counts.push(value.length); } catch (_) {}
    try { if (value.length != null && Number(value.length) > 0) counts.push(Number(value.length)); } catch (_) {}
    try { if (value._source && Array.isArray(value._source)) counts.push(value._source.length); } catch (_) {}
    try { if (value.source && Array.isArray(value.source)) counts.push(value.source.length); } catch (_) {}
    try {
      if (value.$dataProvider && value.$dataProvider._source && Array.isArray(value.$dataProvider._source)) {
        counts.push(value.$dataProvider._source.length);
      }
    } catch (_) {}
    try { if (value.$children && Array.isArray(value.$children)) counts.push(value.$children.length); } catch (_) {}
    return Math.max(0, ...counts.filter((item) => Number.isFinite(item)));
  }

  const app = window.application || null;
  const scene = app && app._currentScene;
  const prev = app && app._prevScene;
  const sceneName = scene && scene.constructor ? String(scene.constructor.name || "") : "";
  const prevName = prev && prev.constructor ? String(prev.constructor.name || "") : "";
  const roots = [];
  try { roots.push(scene && scene.gameList); } catch (_) {}
  try { roots.push(scene && scene.gameList0); } catch (_) {}
  try { roots.push(scene && scene.gameListData); } catch (_) {}
  try { roots.push(prev && prev.gameList); } catch (_) {}
  try { roots.push(prev && prev.gameList0); } catch (_) {}
  try { roots.push(prev && prev.gameListData); } catch (_) {}
  try { roots.push(scene && scene.$Component && scene.$Component[8] && scene.$Component[8].gameList0); } catch (_) {}
  try { roots.push(prev && prev.$Component && prev.$Component[8] && prev.$Component[8].gameList0); } catch (_) {}

  let roomCount = 0;
  for (const root of roots) roomCount = Math.max(roomCount, countRooms(root));

  let canvasRect = null;
  try {
    const canvas = document.querySelector("canvas");
    if (canvas) {
      const rect = canvas.getBoundingClientRect();
      canvasRect = { width: Math.round(rect.width || 0), height: Math.round(rect.height || 0) };
    }
  } catch (_) {}

  const sceneText = `${sceneName} ${prevName}`;
  return {
    href: String(location.href || "").slice(0, 160),
    ready_state: document.readyState,
    has_application: !!app,
    scene_name: sceneName,
    prev_scene_name: prevName,
    room_count: roomCount,
    canvas_rect: canvasRect,
    hall_ready: /RoomHall|Hall/i.test(sceneText) || roomCount >= 4,
    game_ready: /GameScene|BjlGameScene/i.test(sceneName),
  };
}
"""


LOCAL_STORAGE_DUMP_JS = r"""
() => {
  const items = [];
  try {
    for (let index = 0; index < localStorage.length; index += 1) {
      const key = localStorage.key(index);
      if (key != null) items.push([String(key), String(localStorage.getItem(key) || "")]);
    }
  } catch (_) {}
  return items;
}
"""


ROOM_ENTRY_TARGET_JS = r"""
({ roomIndex }) => {
  const index = Number(roomIndex);
  const baseSize = { width: 960, height: 620 };
  const targets = [
    { x: 858, y: 268 },
    { x: 858, y: 360 },
    { x: 858, y: 451 },
    { x: 858, y: 542 },
  ];
  if (!Number.isFinite(index) || index < 1 || index > targets.length) {
    return { ok: false, reason: "invalid_room_index", room_index: index };
  }
  const canvas = document.querySelector("canvas");
  if (!canvas) return { ok: false, reason: "canvas_not_found", room_index: index };
  const rect = canvas.getBoundingClientRect();
  if (!rect || rect.width <= 0 || rect.height <= 0) {
    return { ok: false, reason: "canvas_rect_invalid", room_index: index };
  }
  const base = targets[index - 1];
  const x = rect.left + (base.x / baseSize.width) * rect.width;
  const y = rect.top + (base.y / baseSize.height) * rect.height;
  return {
    ok: true,
    room_index: index,
    base_size: baseSize,
    base_point: { x: base.x, y: base.y },
    x: Math.round(x),
    y: Math.round(y),
    canvas_rect: {
      left: Math.round(rect.left),
      top: Math.round(rect.top),
      width: Math.round(rect.width),
      height: Math.round(rect.height)
    },
  };
}
"""


_HALL_ENTRY_OFFSETS: tuple[tuple[int, int], ...] = (
    (0, 0),
    (0, 24),
    (0, 36),
    (0, 48),
    (-18, 24),
    (-36, 24),
    (18, 24),
    (36, 24),
    (-18, 0),
    (-36, 0),
    (18, 0),
    (36, 0),
    (-54, 24),
    (54, 24),
    (0, 12),
    (0, -12),
)


@dataclass(frozen=True)
class LaunchUrlCandidate:
    url: str
    score: int
    page_index: int = -1
    frame_index: int = -1

    def safe_summary(self) -> dict[str, Any]:
        payload = safe_launch_url_summary(self.url)
        payload["score"] = self.score
        if self.page_index >= 0:
            payload["page_index"] = self.page_index
        if self.frame_index >= 0:
            payload["frame_index"] = self.frame_index
        return payload


@dataclass(frozen=True)
class BaccaratLoadState:
    ready: bool
    hall_ready: bool
    game_ready: bool
    has_application: bool
    scene_name: str = ""
    prev_scene_name: str = ""
    room_count: int = 0
    ready_state: str = ""
    frame_index: int = -1
    canvas_rect: dict[str, Any] | None = None

    def safe_summary(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "hall_ready": self.hall_ready,
            "game_ready": self.game_ready,
            "has_application": self.has_application,
            "scene_name": self.scene_name,
            "prev_scene_name": self.prev_scene_name,
            "room_count": self.room_count,
            "ready_state": self.ready_state,
            "frame_index": self.frame_index,
            "canvas_rect": self.canvas_rect or {},
        }


def looks_like_game_launch_url(url: str, *, expected_game_id: str = "910") -> bool:
    text = str(url or "").strip()
    lowered = text.lower()
    if not lowered.startswith(("http://", "https://")):
        return False
    if _STATIC_ASSET_RE.search(lowered):
        return False
    query = dict(parse_qsl(urlsplit(text).query, keep_blank_values=True))
    if expected_game_id and query.get("gameId") != expected_game_id:
        return False
    return bool(query.get("token") and query.get("route") and query.get("gameId"))


def safe_launch_url_summary(url: str) -> dict[str, Any]:
    text = str(url or "")
    parsed = urlsplit(text)
    query_keys = [key for key, _ in parse_qsl(parsed.query, keep_blank_values=True)]
    return {
        "url_hash": hashlib.sha256(text.encode("utf-8")).hexdigest()[:16] if text else "",
        "host": parsed.netloc,
        "path": parsed.path or "/",
        "query_keys": query_keys,
        "sensitive_keys": [key for key in query_keys if _SENSITIVE_QUERY_RE.search(key)],
        "length": len(text),
    }


async def extract_game_launch_url_from_page(
    page: Any,
    *,
    expected_game_id: str = "910",
) -> LaunchUrlCandidate | None:
    candidates = await collect_game_launch_url_candidates(page, expected_game_id=expected_game_id)
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: (item.score, len(item.url)), reverse=True)[0]


async def collect_game_launch_url_candidates(
    page: Any,
    *,
    expected_game_id: str = "910",
) -> list[LaunchUrlCandidate]:
    candidates: list[LaunchUrlCandidate] = []
    seen: set[str] = set()
    try:
        context_pages = [candidate for candidate in list(page.context.pages) if not candidate.is_closed()]
        pages = [page] + [candidate for candidate in context_pages if candidate is not page]
    except Exception:
        pages = [page]
    for page_index, candidate_page in enumerate(pages):
        try:
            frames = list(candidate_page.frames)
        except Exception:
            frames = []
        for frame_index, frame in enumerate(frames):
            try:
                current_frame_url = str(frame.url or "")
            except Exception:
                current_frame_url = ""
            urls = await _read_frame_urls(frame)
            for url in urls:
                if not looks_like_game_launch_url(url, expected_game_id=expected_game_id):
                    continue
                if url in seen:
                    continue
                seen.add(url)
                candidates.append(
                    LaunchUrlCandidate(
                        url=url,
                        score=(
                            _launch_url_score(url)
                            + (500 if candidate_page is page else 0)
                            + (1000 if url == current_frame_url else 0)
                        ),
                        page_index=page_index,
                        frame_index=frame_index,
                    )
                )
    return candidates


async def read_baccarat_load_state(page: Any) -> BaccaratLoadState:
    best = BaccaratLoadState(
        ready=False,
        hall_ready=False,
        game_ready=False,
        has_application=False,
    )
    try:
        frames = list(page.frames)
    except Exception:
        frames = []
    for frame_index, frame in enumerate(frames):
        try:
            raw = await frame.evaluate(BACCARAT_LOAD_STATE_JS)
        except Exception:
            continue
        state = _load_state_from_raw(raw, frame_index=frame_index)
        if _load_state_score(state) > _load_state_score(best):
            best = state
    return best


async def capture_local_storage_for_candidate(
    page: Any,
    candidate: LaunchUrlCandidate,
) -> list[list[str]]:
    frame = _candidate_frame(page, candidate)
    if frame is None:
        return []
    try:
        raw = await frame.evaluate(LOCAL_STORAGE_DUMP_JS)
    except Exception:
        return []
    items: list[list[str]] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, list) or len(item) < 2:
            continue
        key = item[0]
        value = item[1]
        if key is None or value is None:
            continue
        items.append([str(key), str(value)])
    return items


async def install_local_storage_init_script(context: Any, items: list[list[str]]) -> None:
    if not items:
        return
    payload = json.dumps(items, ensure_ascii=False).replace("<", "\\u003c")
    await context.add_init_script(
        """
(() => {
  const items = __BET_DESKTOP_LOCAL_STORAGE__;
  try {
    for (const pair of items) {
      if (Array.isArray(pair) && pair.length >= 2 && pair[0] != null && pair[1] != null) {
        localStorage.setItem(String(pair[0]), String(pair[1]));
      }
    }
  } catch (_) {}
})();
""".replace("__BET_DESKTOP_LOCAL_STORAGE__", payload)
    )


async def enter_baccarat_hall_room(page: Any, room_index: int) -> dict[str, Any]:
    """Click one of the four room-entry buttons in the baccarat hall canvas."""

    candidates = await resolve_baccarat_hall_room_entry_candidates(page, room_index)
    if not candidates:
        return {"ok": False, "reason": "no_room_entry_candidates", "room_index": int(room_index)}
    target = candidates[0]
    await page.mouse.click(int(target["x"]), int(target["y"]))
    return target


async def resolve_baccarat_hall_room_entry_candidates(page: Any, room_index: int) -> list[dict[str, Any]]:
    """Return page-coordinate click candidates for one hall room entry.

    The game may be rendered inside a child frame. Canvas coordinates read from
    that frame are frame-local, while Playwright mouse clicks are page-local, so
    every candidate includes the frame element offset when needed.
    """

    try:
        index = int(room_index)
    except (TypeError, ValueError):
        return []
    if index not in {1, 2, 3, 4}:
        return []
    buttons = await resolve_baccarat_hall_entry_buttons(page)
    target = next((item for item in buttons if item.get("room_index") == index), None)
    if not target:
        return []

    candidates: list[dict[str, Any]] = []
    base_x = int(target["x"])
    base_y = int(target["y"])
    for attempt_index, (dx, dy) in enumerate(_HALL_ENTRY_OFFSETS):
        candidate = dict(target)
        candidate.update(
            {
                "attempt_index": attempt_index,
                "x": base_x + dx,
                "y": base_y + dy,
                "dx": dx,
                "dy": dy,
            }
        )
        candidates.append(candidate)
    return candidates


async def resolve_baccarat_hall_entry_buttons(page: Any) -> list[dict[str, Any]]:
    """Return the four primary page-coordinate room-entry button centers."""

    try:
        frames = list(page.frames)
    except Exception:
        frames = []
    if not frames:
        return []

    for frame_index, frame in enumerate(frames):
        try:
            raw_state = await frame.evaluate(BACCARAT_LOAD_STATE_JS)
        except Exception:
            raw_state = {}
        state = _load_state_from_raw(raw_state if isinstance(raw_state, dict) else {}, frame_index=frame_index)
        if not state.hall_ready:
            continue
        buttons: list[dict[str, Any]] = []
        frame_offset = await _frame_page_offset(page, frame)
        for index in (1, 2, 3, 4):
            try:
                raw_target = await frame.evaluate(ROOM_ENTRY_TARGET_JS, {"roomIndex": index})
            except Exception:
                raw_target = {}
            if not isinstance(raw_target, dict) or not raw_target.get("ok"):
                buttons = []
                break
            canvas_rect = raw_target.get("canvas_rect") if isinstance(raw_target.get("canvas_rect"), dict) else {}
            page_canvas_rect = {
                "left": int(canvas_rect.get("left") or 0) + int(frame_offset.get("x", 0)),
                "top": int(canvas_rect.get("top") or 0) + int(frame_offset.get("y", 0)),
                "width": int(canvas_rect.get("width") or 0),
                "height": int(canvas_rect.get("height") or 0),
            }
            buttons.append(
                {
                    "ok": True,
                    "source": "canvas_ratio",
                    "room_index": index,
                    "frame_index": frame_index,
                    "x": int(raw_target["x"]) + int(frame_offset.get("x", 0)),
                    "y": int(raw_target["y"]) + int(frame_offset.get("y", 0)),
                    "base_size": raw_target.get(
                        "base_size",
                        {"width": HALL_ENTRY_BASE_WIDTH, "height": HALL_ENTRY_BASE_HEIGHT},
                    ),
                    "base_point": raw_target.get("base_point", {}),
                    "scene_name": state.scene_name,
                    "room_count": state.room_count,
                    "frame_offset": frame_offset,
                    "canvas_rect": page_canvas_rect,
                }
            )
        if len(buttons) == 4:
            return buttons
    return []


async def wait_for_baccarat_hall_entry_buttons(
    page: Any,
    *,
    timeout_ms: int = 90000,
    poll_ms: int = 500,
) -> tuple[BaccaratLoadState, list[dict[str, Any]]]:
    deadline = asyncio.get_running_loop().time() + max(1, timeout_ms) / 1000.0
    last = BaccaratLoadState(False, False, False, False)
    last_buttons: list[dict[str, Any]] = []
    while True:
        last = await read_baccarat_load_state(page)
        if last.hall_ready:
            last_buttons = await resolve_baccarat_hall_entry_buttons(page)
            if len(last_buttons) == 4:
                return last, last_buttons
        if asyncio.get_running_loop().time() >= deadline:
            return last, last_buttons
        await page.wait_for_timeout(max(100, int(poll_ms)))


async def _frame_page_offset(page: Any, frame: Any) -> dict[str, Any]:
    try:
        if frame == page.main_frame:
            return {"x": 0, "y": 0, "source": "main_frame"}
    except Exception:
        pass
    try:
        element = await frame.frame_element()
        box = await element.bounding_box()
    except Exception:
        box = None
    if isinstance(box, dict):
        return {
            "x": int(round(float(box.get("x") or 0))),
            "y": int(round(float(box.get("y") or 0))),
            "width": int(round(float(box.get("width") or 0))),
            "height": int(round(float(box.get("height") or 0))),
            "source": "frame_element",
        }
    return {"x": 0, "y": 0, "source": "unknown"}


async def wait_for_baccarat_ready(
    page: Any,
    *,
    timeout_ms: int = 90000,
    poll_ms: int = 500,
) -> BaccaratLoadState:
    deadline = asyncio.get_running_loop().time() + max(1, timeout_ms) / 1000.0
    last = BaccaratLoadState(False, False, False, False)
    while True:
        last = await read_baccarat_load_state(page)
        if last.ready:
            return last
        if asyncio.get_running_loop().time() >= deadline:
            return last
        await page.wait_for_timeout(max(100, int(poll_ms)))


def _candidate_frame(page: Any, candidate: LaunchUrlCandidate) -> Any | None:
    try:
        pages = [candidate_page for candidate_page in list(page.context.pages) if not candidate_page.is_closed()]
    except Exception:
        pages = [page]
    if not (0 <= candidate.page_index < len(pages)):
        return None
    try:
        frames = list(pages[candidate.page_index].frames)
    except Exception:
        return None
    if not (0 <= candidate.frame_index < len(frames)):
        return None
    return frames[candidate.frame_index]


async def _read_frame_urls(frame: Any) -> list[str]:
    urls: list[str] = []
    try:
        if frame.url:
            urls.append(str(frame.url))
    except Exception:
        pass
    try:
        raw = await frame.evaluate(LAUNCH_URLS_JS)
    except Exception:
        raw = []
    if isinstance(raw, list):
        urls.extend(str(item) for item in raw if isinstance(item, str))
    return urls


def _launch_url_score(url: str) -> int:
    query = dict(parse_qsl(urlsplit(url).query, keep_blank_values=True))
    score = 0
    score += 60 if query.get("gameId") == "910" else 0
    score += 50 if query.get("token") else 0
    score += 25 if query.get("route") else 0
    score += 20 if query.get("account") else 0
    score += 15 if query.get("extraParam") else 0
    score += min(len(url) // 100, 20)
    return score


def _load_state_from_raw(raw: Any, *, frame_index: int) -> BaccaratLoadState:
    if not isinstance(raw, dict):
        return BaccaratLoadState(False, False, False, False, frame_index=frame_index)
    hall_ready = bool(raw.get("hall_ready"))
    game_ready = bool(raw.get("game_ready"))
    return BaccaratLoadState(
        ready=hall_ready or game_ready,
        hall_ready=hall_ready,
        game_ready=game_ready,
        has_application=bool(raw.get("has_application")),
        scene_name=str(raw.get("scene_name") or "")[:80],
        prev_scene_name=str(raw.get("prev_scene_name") or "")[:80],
        room_count=_as_int(raw.get("room_count")),
        ready_state=str(raw.get("ready_state") or "")[:40],
        frame_index=frame_index,
        canvas_rect=raw.get("canvas_rect") if isinstance(raw.get("canvas_rect"), dict) else None,
    )


def _load_state_score(state: BaccaratLoadState) -> int:
    return (
        int(state.ready) * 100
        + int(state.hall_ready) * 50
        + int(state.game_ready) * 50
        + int(state.has_application) * 20
        + int(state.room_count)
    )


def _as_int(value: Any) -> int:
    try:
        return int(float(value))
    except Exception:
        return 0

