"""Read-only frontend JSON.parse state probe for live UI synchronization."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


FRONTEND_STATE_PROBE_JS = r"""
(() => {
  if (window.__betDesktopFrontendStateProbeInstalled) return;
  window.__betDesktopFrontendStateProbeInstalled = true;

  const sensitive = /(token|cookie|pass|pwd|auth|secret|session|credential|jwt|key)/i;
  const stateKeys = /(gameNo|gamesn|gameSn|round|issue|batch|shoe|countdown|timed|time|left|remain|RealTime|realTime|action|actionFlag|currentLoadType|isCanBetting|selectedBet|chouMaAllCount|bet|status|state|phase|room|roomId|roomNo|table|tableId|tableNo|desk|cid|sn|limit|quota|red|balance|wallet|credit|money|amount|min|max|userMinbet|userMaxbet)/i;
  const events = [];
  const MAX_EVENTS = 300;

  function safeValue(path, value) {
    if (sensitive.test(path)) return "[REDACTED]";
    if (value == null) return value;
    const type = typeof value;
    if (type === "number" || type === "boolean") return value;
    if (type === "string") return value.length > 160 ? value.slice(0, 160) : value;
    return `[${Object.prototype.toString.call(value)}]`;
  }

  function pushEvent(event) {
    try {
      event.ts = Date.now();
      event.perf = Math.round(performance.now());
      events.push(event);
      if (events.length > MAX_EVENTS) events.splice(0, events.length - MAX_EVENTS);
    } catch (_) {}
  }

  function summarizeObject(root, source) {
    const hits = [];
    const contexts = [];
    const seen = new WeakSet();
    const queue = [{ value: root, path: source, depth: 0 }];
    let budget = 0;
    while (queue.length && budget < 4000 && hits.length < 180) {
      budget += 1;
      const item = queue.shift();
      const value = item.value;
      if (!value || typeof value !== "object") continue;
      if (seen.has(value)) continue;
      seen.add(value);
      if (value.nodeType || value === window || value === document || value === location) continue;
      let names = [];
      try { names = Object.getOwnPropertyNames(value).slice(0, 180); } catch (_) { continue; }
      const contextFields = [];
      let contextScore = 0;
      for (const name of names) {
        const key = String(name);
        const path = `${item.path}.${key}`;
        if (sensitive.test(path)) continue;
        let child;
        try { child = value[name]; } catch (_) { continue; }
        const childType = typeof child;
        if (stateKeys.test(key) && (child == null || childType === "number" || childType === "boolean" || childType === "string")) {
          const hit = { path, key: key.slice(0, 80), type: childType, value: safeValue(path, child) };
          hits.push(hit);
          contextFields.push(hit);
          if (/^(gameNo|gamesn|gameSn|round|issue|batch)$/i.test(key)) contextScore += 3;
          if (/^(countdown|timed|time|left|remain|RealTime|realTime)$/i.test(key)) contextScore += 3;
          if (/^(action|actionFlag|currentLoadType|isCanBetting|status|state|phase)$/i.test(key)) contextScore += 2;
          if (/^(room|roomId|roomNo|table|tableId|tableNo|desk|cid|sn)$/i.test(key)) contextScore += 2;
          if (/^(limit|quota|red|redLimit|min|max|userMinbet|userMaxbet|balance|wallet|credit|money|amount)$/i.test(key)) contextScore += 1;
          if (hits.length >= 180) break;
        }
        if (item.depth < 5 && child && childType === "object" && !child.nodeType) {
          queue.push({ value: child, path, depth: item.depth + 1 });
        }
      }
      if (contextFields.length >= 2 && contextScore >= 4 && contexts.length < 60) {
        contexts.push({ path: item.path.slice(0, 220), score: contextScore, fields: contextFields.slice(0, 80) });
      }
    }
    if (hits.length) pushEvent({ event: "json_parse_state", source, hits, contexts });
  }

  function summarizeTimedContext(root, source) {
    const seen = new WeakSet();
    const queue = [{ value: root, path: source, depth: 0 }];
    let budget = 0;
    let emitted = 0;
    while (queue.length && budget < 1800 && emitted < 24) {
      budget += 1;
      const item = queue.shift();
      const value = item.value;
      if (!value || typeof value !== "object") continue;
      if (seen.has(value)) continue;
      seen.add(value);
      if (value.nodeType || value === window || value === document || value === location) continue;
      let names = [];
      try { names = Object.getOwnPropertyNames(value).slice(0, 140); } catch (_) { continue; }
      const hasTimer = names.some((name) => /^(countdown|timed|time|left|remain|remainingTime|betTime|RealTime|realTime)$/i.test(String(name)));
      const hasAction = names.some((name) => /^(action|actionFlag|currentLoadType|isCanBetting|status|state|phase)$/i.test(String(name)));
      const hasGame = names.some((name) => /^(gameNo|gamesn|gameSn|sn|round|issue|batch)$/i.test(String(name)));
      if (hasTimer || (hasAction && hasGame)) {
        const fields = [];
        for (const name of names) {
          const key = String(name);
          if (sensitive.test(key)) continue;
          let child;
          try { child = value[name]; } catch (_) { continue; }
          const childType = typeof child;
          if (child == null || childType === "number" || childType === "boolean" || childType === "string") {
            fields.push({ key: key.slice(0, 80), type: childType, value: safeValue(`${item.path}.${key}`, child) });
          }
          if (fields.length >= 100) break;
        }
        pushEvent({ event: "timed_context", source, path: item.path.slice(0, 220), fields });
        emitted += 1;
      }
      if (item.depth < 4) {
        for (const name of names) {
          const key = String(name);
          if (sensitive.test(key)) continue;
          let child;
          try { child = value[name]; } catch (_) { continue; }
          if (child && typeof child === "object" && !child.nodeType) {
            queue.push({ value: child, path: `${item.path}.${key}`, depth: item.depth + 1 });
          }
        }
      }
    }
  }

  const originalJSONParse = JSON.parse;
  JSON.parse = function(text, reviver) {
    const result = originalJSONParse.apply(this, arguments);
    try { summarizeObject(result, "JSON.parse"); } catch (_) {}
    try { summarizeTimedContext(result, "JSON.parse"); } catch (_) {}
    return result;
  };

  window.__betDesktopDrainFrontendStateEvents = () => events.splice(0, events.length);
})();
"""


FRONTEND_OBJECT_SCAN_JS = r"""
() => {
  const sensitive = /(token|cookie|pass|pwd|auth|secret|session|credential|jwt|key)/i;
  const roots = [];
  function add(name, getter) {
    try {
      const value = getter();
      if (value && typeof value === "object") roots.push([name, value]);
    } catch (_) {}
  }
  add("application._prevScene.gameList", () => window.application && window.application._prevScene && window.application._prevScene.gameList);
  add("application._currentScene.gameList", () => window.application && window.application._currentScene && window.application._currentScene.gameList);
  add("application.currentScene", () => window.application && window.application._currentScene);
  add("application.prevScene", () => window.application && window.application._prevScene);
  add("application", () => window.application);
  add("application.app", () => window.application && window.application._app);
  add("playerData", () => window.playerData);
  const keys = /^(gameNo|gamesn|gameSn|sn|roomId|roomType|roomNo|tableNo|countdown|timed|time|RealTime|realTime|action|actionFlag|currentLoadType|isCanBetting|userMinbet|userMaxbet|selectedBet|chouMaAllCount|status|state|phase)$/i;
  const hits = [];
  const seen = new WeakSet();

  function visit(path, value, depth) {
    if (!value || typeof value !== "object" || depth > 7 || hits.length > 260) return;
    if (seen.has(value)) return;
    seen.add(value);
    if (value.nodeType || value === window || value === document || value === location) return;
    let names = [];
    try { names = Object.getOwnPropertyNames(value).slice(0, 180); } catch (_) { return; }
    for (const name of names) {
      const key = String(name);
      const nextPath = `${path}.${key}`;
      if (sensitive.test(nextPath)) continue;
      let child;
      try { child = value[name]; } catch (_) { continue; }
      const type = typeof child;
      if (keys.test(key) && (child == null || type === "number" || type === "boolean" || type === "string")) {
        hits.push({ path: nextPath, key, value: child, type });
      }
      if (child && type === "object" && !child.nodeType) visit(nextPath, child, depth + 1);
      if (hits.length > 260) break;
    }
  }

  for (const [name, root] of roots) visit(name, root, 0);
  return { event: "object_scan", ts: Date.now(), source: "object_scan", hits };
}
"""


FRONTEND_BOUND_ROOM_SCAN_JS = r"""
() => {
  const sensitive = /(token|cookie|pass|pwd|auth|secret|session|credential|jwt|key)/i;
  const candidates = [];
  const seen = new WeakSet();

  function safeValue(path, value) {
    if (sensitive.test(path)) return "[REDACTED]";
    if (value == null) return value;
    const type = typeof value;
    if (type === "number" || type === "boolean") return value;
    if (type === "string") return value.length > 160 ? value.slice(0, 160) : value;
    return null;
  }

  function ownNames(value) {
    try { return Object.getOwnPropertyNames(value).slice(0, 220); } catch (_) { return []; }
  }

  function primitiveFields(path, value) {
    const fields = [];
    for (const name of ownNames(value)) {
      const key = String(name);
      const fieldPath = `${path}.${key}`;
      if (sensitive.test(fieldPath)) continue;
      let child;
      try { child = value[name]; } catch (_) { continue; }
      const type = typeof child;
      if (child == null || type === "number" || type === "boolean" || type === "string") {
        const safe = safeValue(fieldPath, child);
        if (safe !== null) fields.push({ path: fieldPath, key, value: safe, type });
      }
    }
    return fields;
  }

  function scoreFields(path, fields) {
    let score = 0;
    const loweredPath = String(path).toLowerCase();
    if (/history|records?|road|trend|result/.test(loweredPath)) score -= 180;
    if (loweredPath.includes("gamelist")) score += 40;
    if (loweredPath.includes("dataprovider") || loweredPath.includes("_source")) score += 35;
    for (const item of fields) {
      const key = String(item.key || "").toLowerCase();
      const value = String(item.value == null ? "" : item.value);
      if (/^sn$/.test(key) && /^\d{6,12}$/.test(value)) score += 40;
      if (/^(roomid|roomtype|roomno|tableno|tableid)$/.test(key)) score += 35;
      if (/^(gameno|gamesn|gamesn)$/.test(key)) score += 35;
      if (/^(countdown|timed|realtime|realTime)$/i.test(item.key || "")) score += 30;
      if (/^(action|currentloadtype|iscanbetting)$/i.test(item.key || "")) score += 25;
      if (/^(userminbet|usermaxbet)$/i.test(item.key || "")) score += 30;
      if (/^T\d{3,4}$/i.test(value) || /^\d{6,12}$/.test(value)) score += 50;
    }
    return score;
  }

  function visit(path, value, depth) {
    if (!value || typeof value !== "object" || depth > 8 || candidates.length > 80) return;
    if (seen.has(value)) return;
    seen.add(value);
    if (value.nodeType || value === window || value === document || value === location) return;
    const fields = primitiveFields(path, value);
    const score = scoreFields(path, fields);
    if (fields.length >= 2 && score >= 80) {
      candidates.push({ path: path.slice(0, 220), score, fields: fields.slice(0, 140) });
    }
    for (const name of ownNames(value)) {
      const key = String(name);
      const nextPath = `${path}.${key}`;
      if (sensitive.test(nextPath)) continue;
      let child;
      try { child = value[name]; } catch (_) { continue; }
      if (child && typeof child === "object" && !child.nodeType) visit(nextPath, child, depth + 1);
    }
  }

  try { if (window.application && window.application._prevScene) visit("application._prevScene", window.application._prevScene, 0); } catch (_) {}
  try { if (window.application && window.application._currentScene) visit("application._currentScene", window.application._currentScene, 0); } catch (_) {}
  candidates.sort((a, b) => b.score - a.score);
  return { event: "bound_room_object", ts: Date.now(), source: "bound_room_object", contexts: candidates.slice(0, 12) };
}
"""


ROUND_RE = re.compile(r"(?<![\d-])\d{2,}-\d{6,}-\d{6,}-\d+(?!\d)")
COUNTDOWN_KEYS = {"countdown", "countdownseconds", "leftseconds", "timeleft", "remainseconds", "betseconds", "timed"}
SHORT_GAME_NO_RE = re.compile(r"\d{8,}")
PHASE_TEXT_KEYS = {"phase", "status", "state", "gamestatus", "statustext", "phasetext", "statusname", "statename"}
ROOM_KEYS = {"room", "roomid", "roomtype", "roomno", "roomname", "table", "tableid", "tableno", "desk", "deskid", "deskno", "sn"}
LIMIT_KEYS = {"limit", "limitred", "redlimit", "quota", "xianhong", "minmax", "betlimit"}
USER_MIN_BET_KEYS = {"userminbet", "userminbetcent", "userminbetcents", "user_min_bet", "usermin", "userminlimit"}
USER_MAX_BET_KEYS = {"usermaxbet", "usermaxbetcent", "usermaxbetcents", "user_max_bet", "usermax", "usermaxlimit"}
MIN_BET_KEYS = {"min", "minbet", "minlimit", *USER_MIN_BET_KEYS}
MAX_BET_KEYS = {"max", "maxbet", "maxlimit", *USER_MAX_BET_KEYS}
NEGATIVE_CONTEXT_HINTS = (
    "history",
    "road",
    "trend",
    "record",
    "result",
    "stat",
    "list",
    "cards",
    "poker",
    "chip",
    "chouma",
)
POSITIVE_CONTEXT_HINTS = ("current", "scene", "table", "desk", "room", "game")


def _is_stale_history_context(path: str) -> bool:
    lowered = str(path or "").lower()
    return any(token in lowered for token in ("history", "records", "record.", "road", "trend", "result"))


def _is_live_room_list_context(path: str) -> bool:
    lowered = str(path or "").lower()
    return bool(
        re.search(r"(?:^|\.)gamelist\.\d+(?:\.\d+)?(?:\.|$)", lowered)
        and "$dataprovider" not in lowered
        and "._source" not in lowered
    )


@dataclass(frozen=True)
class FrontendStateEvent:
    timestamp_ms: int
    event_type: str = ""
    batch_id: str = ""
    batch_id_is_full: bool = False
    short_batch_id: str = ""
    batch_rank: tuple[int, int, int, int] = (0, 0, 0, 0)
    countdown: int | None = None
    balance: str = ""
    action: int | None = None
    timed: int | None = None
    selected_bet: int | None = None
    pending_chip_cents: int | None = None
    current_load_type: int | None = None
    is_can_betting: bool | None = None
    real_time: int | None = None
    phase_text: str = ""
    room_label: str = ""
    room_id: str = ""
    limit_label: str = ""
    context_path: str = ""
    confidence: float = 0.0
    matched_paths: list[str] = field(default_factory=list)

    @property
    def has_state(self) -> bool:
        return bool(
            self.batch_id
            or self.countdown is not None
            or self.balance
            or self.action is not None
            or self.timed is not None
            or self.current_load_type is not None
            or self.is_can_betting is not None
            or self.real_time is not None
            or self.phase_text
            or self.room_label
            or self.room_id
            or self.limit_label
        )


def parse_frontend_state_event(raw: dict[str, Any]) -> FrontendStateEvent | None:
    if not isinstance(raw, dict) or raw.get("event") not in {
        "json_parse_state",
        "timed_context",
        "object_scan",
        "bound_room_object",
    }:
        return None
    fields: list[tuple[str, str, Any]] = []
    raw_hits = raw.get("fields") if raw.get("event") == "timed_context" else raw.get("hits")
    default_path = str(raw.get("path") or raw.get("source") or "JSON.parse")
    for hit in raw_hits or []:
        if not isinstance(hit, dict):
            continue
        path = str(hit.get("path") or f"{default_path}.{hit.get('key', '')}")
        key = str(hit.get("key") or path.rsplit(".", 1)[-1])
        value = hit.get("value")
        fields.append((path, key, value))
    if not fields and not raw.get("contexts"):
        return None

    candidate_fields = _candidate_field_groups(raw, fields)
    selected_fields, context_path, confidence = _select_candidate(candidate_fields)
    if _is_stale_history_context(context_path):
        return None
    batch_id, batch_is_full, short_batch_id = _pick_batch_id(selected_fields)
    countdown = _pick_countdown(selected_fields)
    action = _pick_int_by_keys(selected_fields, {"action"})
    if action is None:
        action = _pick_numeric_action_flag(selected_fields)
    timed = _pick_int_by_keys(selected_fields, {"timed"})
    selected_bet = _pick_int_by_keys(selected_fields, {"selectedbet"})
    pending_chip = _pick_int_by_keys(selected_fields, {"choumaallcount", "pendingchip", "pendingbet"})
    current_load_type = _pick_int_by_keys(selected_fields, {"currentloadtype", "loadtype"})
    is_can_betting = _pick_bool_by_keys(selected_fields, {"iscanbetting", "canbet", "canbetting"})
    real_time = _pick_int_by_keys(selected_fields, {"realtime"})
    phase_text = _pick_phase_text(selected_fields)
    room_label = _pick_room_label(selected_fields)
    room_id = _pick_room_id(selected_fields)
    if not room_label:
        room_label = _room_label_from_room_id(room_id)
    limit_label = _pick_limit_label(selected_fields)
    balance = _pick_balance(selected_fields)
    event = FrontendStateEvent(
        timestamp_ms=_safe_int(raw.get("ts")) or 0,
        event_type=str(raw.get("event") or ""),
        batch_id=batch_id,
        batch_id_is_full=batch_is_full,
        short_batch_id=short_batch_id,
        batch_rank=_batch_rank(batch_id),
        countdown=countdown,
        balance=balance,
        action=action,
        timed=timed,
        selected_bet=selected_bet,
        pending_chip_cents=pending_chip,
        current_load_type=current_load_type,
        is_can_betting=is_can_betting,
        real_time=real_time,
        phase_text=phase_text,
        room_label=room_label,
        room_id=room_id,
        limit_label=limit_label,
        context_path=context_path,
        confidence=confidence,
        matched_paths=[path[:180] for path, _key, _value in selected_fields[:40]],
    )
    return event if event.has_state else None


def _candidate_field_groups(
    raw: dict[str, Any], fields: list[tuple[str, str, Any]]
) -> list[tuple[str, list[tuple[str, str, Any]]]]:
    groups: list[tuple[str, list[tuple[str, str, Any]]]] = []
    for context in raw.get("contexts") or []:
        if not isinstance(context, dict):
            continue
        path = str(context.get("path") or "")
        context_fields: list[tuple[str, str, Any]] = []
        for hit in context.get("fields") or []:
            if not isinstance(hit, dict):
                continue
            key = str(hit.get("key") or "")
            hit_path = str(hit.get("path") or f"{path}.{key}")
            context_fields.append((hit_path, key, hit.get("value")))
        if context_fields:
            groups.append((path, context_fields))
    if not groups and raw.get("event") == "object_scan":
        by_parent: dict[str, list[tuple[str, str, Any]]] = {}
        for path, key, value in fields:
            parent = path.rsplit(".", 1)[0] if "." in path else str(raw.get("source") or "object_scan")
            by_parent.setdefault(parent, []).append((path, key, value))
        groups.extend(by_parent.items())
        if groups:
            return groups
    groups.append((str(raw.get("path") or raw.get("source") or raw.get("event") or "JSON.parse"), fields))
    return groups


def _select_candidate(
    groups: list[tuple[str, list[tuple[str, str, Any]]]]
) -> tuple[list[tuple[str, str, Any]], str, float]:
    best_score = -10_000
    best_path = ""
    best_fields: list[tuple[str, str, Any]] = []
    for path, fields in groups:
        score = _score_candidate(path, fields)
        if score > best_score:
            best_score = score
            best_path = path
            best_fields = fields
    confidence = max(0.0, min(1.0, best_score / 140.0))
    return best_fields, best_path[:220], confidence


def _score_candidate(path: str, fields: list[tuple[str, str, Any]]) -> int:
    batch_id, batch_is_full, short_batch_id = _pick_batch_id(fields)
    score = 0
    lowered_path = path.lower()
    room_label = _pick_room_label(fields)
    limit_label = _pick_limit_label(fields)
    if batch_is_full:
        score += 100
    elif short_batch_id:
        score += 25
    if _pick_countdown(fields) is not None:
        score += 35
    if _pick_int_by_keys(fields, {"action"}) is not None or _pick_numeric_action_flag(fields) is not None:
        score += 25
    if _pick_bool_by_keys(fields, {"iscanbetting", "canbet", "canbetting"}) is not None:
        score += 25
    if _pick_int_by_keys(fields, {"realtime"}) is not None:
        score += 10
    if _pick_int_by_keys(fields, {"currentloadtype", "loadtype"}) is not None:
        score += 15
    if _pick_phase_text(fields):
        score += 20
    if room_label:
        score += 20
    if limit_label:
        score += 15
    if room_label and limit_label:
        score += 45
    if _pick_balance(fields):
        score += 10
    if any(hint in lowered_path for hint in POSITIVE_CONTEXT_HINTS):
        score += 15
    if "_currentscene" in lowered_path or "currentscene" in lowered_path:
        score += 20
    if "_prevscene" in lowered_path or "prevscene" in lowered_path:
        if any(token in lowered_path for token in ("gamelist", "room", "scene", "table", "desk")):
            score += 25
    if _is_stale_history_context(path):
        score -= 200
    if _is_live_room_list_context(path):
        score += 160
    elif "$dataprovider" in lowered_path or "._source" in lowered_path:
        score -= 60
    if not batch_is_full and not (room_label or limit_label) and any(hint in lowered_path for hint in NEGATIVE_CONTEXT_HINTS):
        score -= 70
    if batch_id and batch_id == short_batch_id and not batch_is_full:
        score -= 15
    return score


def _pick_batch_id(fields: list[tuple[str, str, Any]]) -> tuple[str, bool, str]:
    short_batch_id = ""
    for path, key, value in fields:
        text = str(value or "")
        match = ROUND_RE.search(text)
        if match:
            full = match.group(0)[:120]
            short = _short_from_full_batch(full)
            return full, True, short
        lowered = key.lower()
        if lowered in {"gameno", "gamesn", "gamesn", "gameid"}:
            if text and len(text) >= 8 and SHORT_GAME_NO_RE.fullmatch(text):
                short_batch_id = short_batch_id or text[:120]
    for path, _key, value in fields:
        if "gameno" in path.lower() or "gamesn" in path.lower():
            text = str(value or "")
            match = ROUND_RE.search(text)
            if match:
                full = match.group(0)[:120]
                return full, True, _short_from_full_batch(full)
            if text and len(text) >= 8 and SHORT_GAME_NO_RE.fullmatch(text):
                short_batch_id = short_batch_id or text[:120]
    return short_batch_id, False, short_batch_id


def _pick_countdown(fields: list[tuple[str, str, Any]]) -> int | None:
    for _path, key, value in fields:
        if key.lower() in COUNTDOWN_KEYS:
            parsed = _safe_int(value)
            if parsed is not None and 0 <= parsed <= 90:
                return parsed
    return None


def _pick_balance(fields: list[tuple[str, str, Any]]) -> str:
    for _path, key, value in fields:
        if key.lower() in {"balance", "wallet", "credit", "money", "availablebalance"}:
            text = str(value or "").strip()
            if re.fullmatch(r"\d+(?:\.\d{1,2})?", text):
                return text[:40]
    return ""


def _pick_phase_text(fields: list[tuple[str, str, Any]]) -> str:
    for _path, key, value in fields:
        if key.lower() in PHASE_TEXT_KEYS:
            text = str(value or "").strip()
            if text and len(text) <= 40:
                return text
    return ""


def _pick_room_label(fields: list[tuple[str, str, Any]]) -> str:
    for path, key, value in fields:
        if key.lower() in ROOM_KEYS:
            text = str(value or "").strip()
            table_label = _table_label_from_roomish_value(path, key, text)
            if table_label:
                return table_label
            if re.fullmatch(r"[A-Za-z]{0,3}\d{1,4}", text):
                if key.lower() in {"roomid", "roomtype"}:
                    return _room_label_from_room_id(text)
                if not text.isdigit():
                    return text.upper()[:20]
    for _path, _key, value in fields:
        text = str(value or "").strip()
        match = re.search(r"\b[A-Za-z]\d{3,4}\b", text)
        if match:
            return match.group(0).upper()[:20]
    return ""


def _pick_room_id(fields: list[tuple[str, str, Any]]) -> str:
    room_keys = {
        "roomid",
        "roomtype",
        "roomno",
        "tableid",
        "table_no",
        "tableno",
        "deskid",
        "desk_no",
        "deskno",
        "sn",
        "snid",
    }
    for _path, key, value in fields:
        lowered_key = key.lower()
        if lowered_key not in room_keys:
            continue
        path_text = str(_path or "").lower()
        if lowered_key in {"sn", "snid"}:
            if not any(token in path_text for token in ("gamelist", "room", "scene", "table", "desk")):
                continue
        text = str(value or "").strip()
        if re.fullmatch(r"\d{1,12}", text):
            return text[:20]
    return ""


def _pick_limit_label(fields: list[tuple[str, str, Any]]) -> str:
    user_low = _pick_int_by_keys(fields, USER_MIN_BET_KEYS)
    user_high = _pick_int_by_keys(fields, USER_MAX_BET_KEYS)
    if user_low is not None and user_high is not None and 0 < user_low <= user_high:
        return _format_limit_values(user_low, user_high)

    for _path, key, value in fields:
        lowered = key.lower()
        text = str(value or "").strip()
        if lowered in LIMIT_KEYS:
            parsed = _limit_from_text(text)
            if parsed:
                return parsed
        if lowered in MIN_BET_KEYS:
            low = _safe_int(value)
            high = _pick_int_by_keys(fields, MAX_BET_KEYS)
            if low is not None and high is not None and 0 < low <= high:
                return _format_limit_values(low, high)
    for path, key, value in fields:
        if re.search(r"limit|quota|red|xianhong|\u9650", f"{path}.{key}", re.I):
            parsed = _limit_from_text(str(value or ""))
            if parsed:
                return parsed
    return ""


def _limit_from_text(text: str) -> str:
    if ROUND_RE.search(str(text or "")):
        return ""
    match = re.search(r"(\d{1,6})\s*[-~至]\s*(\d{1,6})", str(text or ""))
    if not match:
        return ""
    low = int(match.group(1))
    high = int(match.group(2))
    if 0 < low <= high:
        return _format_limit_values(low, high)
    return ""


def _format_limit_values(low: int, high: int) -> str:
    if low >= 100 and high >= 100:
        low_display = low // 100 if low % 100 == 0 else low / 100
        high_display = high // 100 if high % 100 == 0 else high / 100
        return f"{low_display:g}-{high_display:g}"
    return f"{low}-{high}"


def _table_label_from_roomish_value(path: str, key: str, text: str) -> str:
    if re.fullmatch(r"T\d{3,}", text, re.I):
        return text.upper()[:20]
    lowered = f"{path}.{key}".lower()
    if key.lower() == "sn" and "gamelist" in lowered and re.fullmatch(r"\d{6,12}", text):
        return f"T{int(text[-3:]):03d}"
    if key.lower() in {"tableno", "tableid", "deskno", "deskid"} and re.fullmatch(r"\d{1,6}", text):
        return f"T{int(text[-3:]):03d}"
    return ""


def _room_label_from_room_id(text: str) -> str:
    mapping = {
        "9101": "T001",
        "182020001": "T001",
    }
    if text in mapping:
        return mapping[text]
    if re.fullmatch(r"\d{6,12}", text):
        return f"T{int(text[-3:]):03d}"
    return ""


def _pick_int_by_keys(fields: list[tuple[str, str, Any]], keys: set[str]) -> int | None:
    for _path, key, value in fields:
        if key.lower() in keys:
            parsed = _safe_int(value)
            if parsed is not None:
                return parsed
    return None


def _pick_numeric_action_flag(fields: list[tuple[str, str, Any]]) -> int | None:
    for _path, key, value in fields:
        if key.lower() != "actionflag" or isinstance(value, bool):
            continue
        parsed = _safe_int(value)
        if parsed is not None and parsed >= 2:
            return parsed
    return None


def _short_from_full_batch(value: str) -> str:
    parts = str(value or "").split("-")
    if len(parts) >= 3:
        return parts[2]
    return ""


def _batch_rank(value: str) -> tuple[int, int, int, int]:
    parts = str(value or "").split("-")
    if len(parts) < 4:
        return (0, 0, 0, 0)
    parsed: list[int] = []
    for part in parts[:4]:
        try:
            parsed.append(int(part))
        except Exception:
            parsed.append(0)
    return tuple(parsed)  # type: ignore[return-value]


def _pick_bool_by_keys(fields: list[tuple[str, str, Any]], keys: set[str]) -> bool | None:
    for _path, key, value in fields:
        if key.lower() in keys:
            return _safe_bool(value)
    return None


def _safe_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(float(value))
    except Exception:
        return None


def _safe_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no"}:
            return False
    return None

