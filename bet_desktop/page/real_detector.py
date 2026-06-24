"""Real Playwright page detector for canvas-heavy H5 game states."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol

from bet_desktop.core.exceptions import AutomationErrorCode, AutomationException
from bet_desktop.core.page_state import PageStateSnapshot


class RealPageLike(Protocol):
    async def evaluate(self, expression: str, *args: Any) -> Any:
        """Evaluate JavaScript in a Playwright-like page."""


class TimeoutRiskException(AutomationException):
    """Raised when countdown is below the configured transaction safety line."""

    def __init__(self, countdown_seconds: int, threshold_seconds: int) -> None:
        super().__init__(
            AutomationErrorCode.COUNTDOWN_UNSAFE,
            "countdown is below safety threshold",
            {
                "countdown_seconds": countdown_seconds,
                "threshold_seconds": threshold_seconds,
            },
        )


@dataclass(frozen=True)
class RealDetectorConfig:
    """Configuration for production page state detection."""

    min_safe_countdown_seconds: int = 3
    enforce_safe_countdown: bool = True
    batch_regex: str = (
        r"(?:牌局编号|局号|局码|期号|靴局|Batch(?: ID)?|Round(?: ID)?|Issue)"
        r"[：:\s#-]*([0-9A-Za-z\-]+)"
    )
    countdown_regex: str = r"(?:倒计时|剩余时间|下注倒计时|Countdown|Time Left)[：:\s]*(\d{1,2})"
    processing_keywords: tuple[str, ...] = ("下注中", "投注中", "Is_Processing", "processing", "betting")
    closed_keywords: tuple[str, ...] = ("准备中", "发牌中", "开牌中", "派彩中", "封盘", "closed")


DEFAULT_STATE_EXTRACTION_JS = r"""
() => {
  const safeGet = (obj, path) => {
    try {
      let cur = obj;
      for (const part of path) {
        if (cur == null) return undefined;
        cur = cur[part];
      }
      return cur;
    } catch (_) {
      return undefined;
    }
  };

  const candidates = [];
  const push = (value, source) => {
    if (value == null) return;
    if (typeof value === "object") {
      candidates.push({ source, value });
    } else {
      candidates.push({ source, value: String(value) });
    }
  };

  push(window.__BET_DESKTOP_STATE__, "__BET_DESKTOP_STATE__");
  push(window.__GAME_STATE__, "__GAME_STATE__");
  push(window.gameState, "gameState");
  push(window.GameState, "GameState");
  push(window.__INITIAL_STATE__, "__INITIAL_STATE__");

  const knownPaths = [
    ["ky", "game", "state"],
    ["egret", "gameState"],
    ["app", "store", "state"],
    ["store", "state"],
    ["__NUXT__", "state"],
    ["__NEXT_DATA__", "props", "pageProps"],
  ];
  for (const path of knownPaths) {
    push(safeGet(window, path), path.join("."));
  }

  const bodyText = document.body ? document.body.innerText || "" : "";
  const attrs = {};
  for (const key of ["data-batch-id", "data-issue", "data-countdown", "data-phase"]) {
    const value = document.body && document.body.getAttribute(key);
    if (value != null) attrs[key] = value;
  }

  return {
    title: document.title || "",
    bodyText: bodyText.slice(0, 4000),
    attrs,
    candidates,
  };
}
"""


class RealPageStateDetector:
    """Extract batch, countdown and processing phase from a real Page."""

    def __init__(
        self,
        *,
        config: RealDetectorConfig | None = None,
        extraction_js: str = DEFAULT_STATE_EXTRACTION_JS,
    ) -> None:
        self.config = config or RealDetectorConfig()
        self.extraction_js = extraction_js

    async def __call__(self, page: RealPageLike) -> PageStateSnapshot:
        raw = await page.evaluate(self.extraction_js)
        snapshot = self._parse_raw_state(raw)
        if self.config.enforce_safe_countdown and snapshot.countdown_seconds < self.config.min_safe_countdown_seconds:
            raise TimeoutRiskException(snapshot.countdown_seconds, self.config.min_safe_countdown_seconds)
        return snapshot

    def _parse_raw_state(self, raw: Any) -> PageStateSnapshot:
        raw = raw if isinstance(raw, dict) else {}
        attrs = raw.get("attrs") if isinstance(raw.get("attrs"), dict) else {}
        body = str(raw.get("bodyText", ""))
        title = str(raw.get("title", ""))
        candidates = raw.get("candidates") if isinstance(raw.get("candidates"), list) else []

        batch_id = (
            str(attrs.get("data-batch-id") or attrs.get("data-issue") or "")
            or self._extract_from_candidates(candidates, ("batch_id", "batchId", "issue", "issueNo", "roundId"))
            or self._regex_first(self.config.batch_regex, body)
        )
        countdown_value = (
            str(attrs.get("data-countdown") or "")
            or self._extract_from_candidates(candidates, ("countdown", "countdownSeconds", "leftSeconds", "timeLeft"))
            or self._regex_first(self.config.countdown_regex, body)
        )
        countdown = int(countdown_value) if str(countdown_value).isdigit() else 0

        phase_text = (
            str(attrs.get("data-phase") or "")
            or self._extract_from_candidates(candidates, ("phase", "status", "state", "gameStatus"))
            or body
            or title
        )
        is_processing = self._detect_processing(str(phase_text))
        safe_summary = {
            "batch_id": batch_id,
            "countdown_seconds": countdown,
            "phase_hint": str(phase_text)[:80],
            "source": "real_detector",
        }
        return PageStateSnapshot(
            is_processing=is_processing,
            countdown_seconds=countdown,
            batch_id=batch_id,
            safe_summary=safe_summary,
        )

    def _detect_processing(self, text: str) -> bool:
        lowered = text.lower()
        if any(keyword.lower() in lowered for keyword in self.config.closed_keywords):
            return False
        return any(keyword.lower() in lowered for keyword in self.config.processing_keywords)

    @staticmethod
    def _regex_first(pattern: str, text: str) -> str:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        return match.group(1) if match else ""

    @classmethod
    def _extract_from_candidates(cls, candidates: list[Any], keys: tuple[str, ...]) -> str:
        for item in candidates:
            if not isinstance(item, dict):
                continue
            value = item.get("value")
            found = cls._find_key(value, keys, depth=0)
            if found:
                return str(found)
        return ""

    @classmethod
    def _find_key(cls, value: Any, keys: tuple[str, ...], *, depth: int) -> Any:
        if depth > 4:
            return None
        if isinstance(value, dict):
            for key in keys:
                if key in value and value[key] not in (None, ""):
                    return value[key]
            for child in value.values():
                found = cls._find_key(child, keys, depth=depth + 1)
                if found not in (None, ""):
                    return found
        if isinstance(value, list):
            for child in value[:20]:
                found = cls._find_key(child, keys, depth=depth + 1)
                if found not in (None, ""):
                    return found
        return None

