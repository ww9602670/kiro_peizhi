"""Read-only live game runtime extraction for desktop workers.

This module consolidates the tested probe snippets into reusable helpers for
the desktop UI. It reads browser memory and screenshot geometry only. It does
not click, submit bets, replay packets, or expose credentials.
"""

from __future__ import annotations

import io
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from PIL import Image

from bet_desktop.models.state_temporal_guard import now_ms
from bet_desktop.vision.coordinate_calibration import coordinates_from_runtime_geometry
from bet_desktop.vision.live_game_regions import recognize_live_game_layout_from_png


CANVAS_GEOMETRY_JS = r"""
() => {
  const canvas = Array.from(document.querySelectorAll("canvas")).find((node) => {
    const rect = node.getBoundingClientRect();
    return rect.width > 20 && rect.height > 20;
  });
  if (!canvas) return null;
  const rect = canvas.getBoundingClientRect();
  const stage = {};
  try {
    const main = window.egret && window.egret.MainContext && window.egret.MainContext.instance;
    const egretStage = main && main.stage;
    if (egretStage) {
      stage.width = Number(egretStage.stageWidth || egretStage.$stageWidth || 0) || 0;
      stage.height = Number(egretStage.stageHeight || egretStage.$stageHeight || 0) || 0;
      stage.scaleMode = String(egretStage.scaleMode || "");
    }
  } catch (_) {}
  return {
    url: String(location.href || ""),
    has_application: !!window.application,
    dpr: Number(window.devicePixelRatio || 1) || 1,
    viewport: {
      width: Math.round(window.innerWidth || 0),
      height: Math.round(window.innerHeight || 0),
    },
    canvas_rect: {
      x: Number(rect.x || rect.left || 0) || 0,
      y: Number(rect.y || rect.top || 0) || 0,
      width: Number(rect.width || 0) || 0,
      height: Number(rect.height || 0) || 0,
    },
    canvas_attr: {
      width: Number(canvas.width || 0) || 0,
      height: Number(canvas.height || 0) || 0,
    },
    stage,
  };
}
"""


RUNTIME_STATE_JS = r"""
() => {
  function readPath(path) {
    const parts = path.split(".");
    let cur = window;
    for (const part of parts) {
      if (part === "window") continue;
      if (cur == null) return null;
      try { cur = cur[part]; } catch (_) { return null; }
    }
    return cur;
  }

  function normalizeCents(value) {
    if (value == null || value === "") return null;
    if (typeof value === "number" && Number.isFinite(value)) return Math.round(value);
    if (typeof value === "string") {
      const cleaned = value.replace(/[,\s]/g, "");
      if (/^-?\d+(\.\d+)?$/.test(cleaned)) {
        const n = Number(cleaned);
        if (Number.isFinite(n)) return Math.round(n);
      }
    }
    return null;
  }

  const state = {
    timestamp_ms: Date.now(),
    performance_ms: Math.round(performance.now()),
    url: "",
    scene_name: "",
    scene_ready: false,
    selected_bet: null,
    pending_chip_cents: null,
    balance_cents: null,
    balance_source: "",
    game_no: "",
    action: null,
    timed: null,
    current_load_type: null,
    is_can_betting: null,
    is_unbet_count: null,
    room_id: null,
    table_label: "",
    user_min_bet_cents: null,
    user_max_bet_cents: null,
    has_password_input: false,
    visible_input_count: 0,
    balance_sources: []
  };

  try { state.url = location.href; } catch (_) {}
  try {
    const visibleInputs = Array.from(document.querySelectorAll("input, textarea")).filter((el) => {
      const rect = el.getBoundingClientRect();
      const style = getComputedStyle(el);
      return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none";
    });
    state.visible_input_count = visibleInputs.length;
    state.has_password_input = Boolean(visibleInputs.find((el) => String(el.type || "").toLowerCase() === "password"));
  } catch (_) {}

  const candidates = [
    { name: "playerData.gold", path: "window.playerData.gold" },
    { name: "scene.userPlayerScore", path: "window.application._currentScene.userPlayerScore" },
    { name: "scene.gold", path: "window.application._currentScene.gold" },
    { name: "scene.balance", path: "window.application._currentScene.balance" },
    { name: "playerData.balance", path: "window.playerData.balance" },
    { name: "playerData.money", path: "window.playerData.money" }
  ];
  for (const item of candidates) {
    const raw = readPath(item.path);
    const cents = normalizeCents(raw);
    if (cents != null) {
      state.balance_sources.push({ name: item.name, path: item.path, raw: String(raw), cents });
    }
  }

  try {
    const scene = window.application && window.application._currentScene;
    if (scene) {
      try { state.scene_name = String((scene.constructor && scene.constructor.name) || ""); } catch (_) {}
      state.scene_ready = true;
      if (scene.selectedBet != null) state.selected_bet = Number(scene.selectedBet);
      if (scene.chouMaAllCount != null) state.pending_chip_cents = Number(scene.chouMaAllCount);
      if (scene.gameNo != null) state.game_no = String(scene.gameNo);
      if (!state.game_no && scene.analyticsData && scene.analyticsData.gameNo != null) {
        state.game_no = String(scene.analyticsData.gameNo);
      }
      if (scene.analyticsData && scene.analyticsData.roomId != null) state.room_id = String(scene.analyticsData.roomId);
      if (scene.roomId != null) state.room_id = String(scene.roomId);
      if (scene.roomType != null) state.room_id = String(scene.roomType);
      if (scene.roomNo != null) state.table_label = String(scene.roomNo);
      if (scene.tableNo != null) state.table_label = String(scene.tableNo);
      if (scene.action != null) state.action = Number(scene.action);
      if (state.action == null && scene.actionFlag != null) {
        const actionFlag = Number(scene.actionFlag);
        if (Number.isFinite(actionFlag) && actionFlag >= 2) state.action = actionFlag;
      }
      if (scene.timed != null) state.timed = Number(scene.timed);
      if (state.timed == null && scene.countdown != null) state.timed = Number(scene.countdown);
      if (scene.currentLoadType != null) state.current_load_type = Number(scene.currentLoadType);
      if (scene.isCanBetting != null) state.is_can_betting = Boolean(scene.isCanBetting);
      if (scene.isUnBetCount != null) state.is_unbet_count = Number(scene.isUnBetCount);
    }
  } catch (_) {}

  try {
    const liveModel = window.application && window.application._app && window.application._app.$children
      && window.application._app.$children[0] && window.application._app.$children[0].$children
      && window.application._app.$children[0].$children[0];
    if (liveModel && !/RoomHallSceneView/i.test(state.scene_name)) {
      if (liveModel.userMinbet != null) state.user_min_bet_cents = Number(liveModel.userMinbet);
      if (liveModel.userMaxbet != null) state.user_max_bet_cents = Number(liveModel.userMaxbet);
      if (liveModel.selectedBet != null) state.selected_bet = Number(liveModel.selectedBet);
      if (liveModel.chouMaAllCount != null) state.pending_chip_cents = Number(liveModel.chouMaAllCount);
      if (!state.game_no && liveModel.gameNo != null) state.game_no = String(liveModel.gameNo);
      if (!state.game_no && liveModel.analyticsData && liveModel.analyticsData.gameNo != null) {
        state.game_no = String(liveModel.analyticsData.gameNo);
      }
      if (liveModel.action != null) state.action = Number(liveModel.action);
      if (state.action == null && liveModel.actionFlag != null) {
        const actionFlag = Number(liveModel.actionFlag);
        if (Number.isFinite(actionFlag) && actionFlag >= 2) state.action = actionFlag;
      }
      if (liveModel.timed != null) state.timed = Number(liveModel.timed);
      if (state.timed == null && liveModel.countdown != null) state.timed = Number(liveModel.countdown);
      if (liveModel.currentLoadType != null) state.current_load_type = Number(liveModel.currentLoadType);
      if (liveModel.isCanBetting != null) state.is_can_betting = Boolean(liveModel.isCanBetting);
      if (liveModel.roomType != null) state.room_id = String(liveModel.roomType);
      if (liveModel.roomNo != null) state.table_label = String(liveModel.roomNo);
      if (liveModel.tableNo != null) state.table_label = String(liveModel.tableNo);
    }
  } catch (_) {}

  const preferred = state.balance_sources.find((item) => item.path === "window.application._currentScene.userPlayerScore")
    || state.balance_sources.find((item) => item.path === "window.playerData.gold")
    || state.balance_sources[0];
  if (preferred) {
    state.balance_cents = preferred.cents;
    state.balance_source = preferred.name;
  }
  return state;
}
"""


LABEL_RUNTIME_STATE_JS = r"""
() => {
  const fullBatchRe = /\d{2,}-\d{6,}-\d{6,}-\d+/;
  const phaseRe = /(\u51c6\u5907\u4e2d|\u4e0b\u6ce8\u4e2d|\u5f00\u724c\u4e2d|\u53d1\u724c\u4e2d|\u4eae\u724c\u4e2d|\u7ed3\u7b97\u4e2d|\u6d3e\u5f69\u4e2d|\u7b49\u5f85\u4e2d|\u4f11\u606f\u4e2d|\u5c01\u76d8|\u505c\u6b62\u4e0b\u6ce8)/;
  const roomRe = /(?:\u623f\u95f4\u53f7|\u623f\u53f7)\s*([A-Z]\d{3,})/i;
  const limitRe = /(?:\u9650\u7ea2|\u9650\u989d)\s*(\d{1,5}(?:\.\d+)?\s*[-~]\s*\d{1,5}(?:\.\d+)?)|(?<![\d-])(\d{1,5}(?:\.\d+)?\s*[-~]\s*\d{1,5}(?:\.\d+)?)(?![\d-])/;
  const countdownKeyRe = /(time|timed|timer|countdown|countDown|left|remain|second|clock|cd)/i;
  const textKeyRe = /(text|string|label|caption|title|name|room|limit|red|status|phase|game|sn|no)/i;
  const badPathRe = /(history|record|road|trend|result|list|chip|chouma|betArea|score|gold|money|balance|wallet|player|user|card|poker|point|endTime|maskTimer|analyticsData|_prevScene|btnChangeScreen)/i;

  function safeString(value) {
    try {
      if (typeof value === "string") return value.trim();
      if (typeof value === "number" && Number.isFinite(value)) return String(value);
    } catch (_) {}
    return "";
  }

  function ownKeys(obj) {
    try {
      const keys = new Set(Object.keys(obj));
      for (const key of Object.getOwnPropertyNames(obj)) keys.add(key);
      return Array.from(keys).slice(0, 140);
    } catch (_) { return []; }
  }

  function readNumber(obj, keys) {
    for (const key of keys) {
      try {
        const value = obj && obj[key];
        if (typeof value === "number" && Number.isFinite(value)) return Math.round(value * 100) / 100;
      } catch (_) {}
    }
    return null;
  }

  function nameOf(obj) {
    try {
      const value = obj && (obj.name || obj._name || obj.id || obj.uuid);
      return typeof value === "string" ? value.slice(0, 80) : "";
    } catch (_) { return ""; }
  }

  function pointOf(obj) {
    try {
      const x = readNumber(obj, ["x", "_x"]);
      const y = readNumber(obj, ["y", "_y"]);
      const width = readNumber(obj, ["width", "_width"]);
      const height = readNumber(obj, ["height", "_height"]);
      if (x != null || y != null || width != null || height != null) {
        return { x, y, width, height };
      }
    } catch (_) {}
    return null;
  }

  function scoreText(path, key, value, meta) {
    let score = 0;
    const combined = `${path}.${key}.${meta.name || ""}`;
    if (fullBatchRe.test(value)) score += 160;
    if (phaseRe.test(value)) score += 130;
    if (roomRe.test(value)) score += 80;
    if (limitRe.test(value)) score += 75;
    if (textKeyRe.test(key) || textKeyRe.test(path) || textKeyRe.test(meta.name || "")) score += 20;
    if (/cc\.director|Laya\.stage|\.stage|children|_children|components|_components/i.test(combined)) score += 45;
    if (/application\._currentScene|application\.currentScene/i.test(combined)) score -= 45;
    if (badPathRe.test(combined)) score -= 65;
    if (value.length > 140) score -= 30;
    return score;
  }

  function scoreNumber(path, key, value, meta) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric) || numeric < 0 || numeric > 90) return -100;
    let score = 0;
    const combined = `${path}.${key}.${meta.name || ""}`;
    if (countdownKeyRe.test(combined)) score += 105;
    if (numeric <= 20) score += 25;
    if (/cc\.director|Laya\.stage|\.stage|children|_children|components|_components/i.test(combined)) score += 45;
    if (/application\._currentScene|application\.currentScene/i.test(combined)) score -= 45;
    if (badPathRe.test(combined)) score -= 75;
    if (/endTime|maskTimer|analyticsData|_prevScene|btnChangeScreen/i.test(combined)) score -= 160;
    if (/chip|bet|score|gold|money|balance|wallet|player|user/i.test(combined)) score -= 80;
    return score;
  }

  function addValue(matches, path, key, raw, meta) {
    const value = safeString(raw);
    if (!value) return;
    const textScore = scoreText(path, key, value, meta);
    const numericScore = /^-?\d+(\.\d+)?$/.test(value) ? scoreNumber(path, key, Number(value), meta) : -100;
    if (textScore <= 0 && numericScore <= 0) return;
    matches.push({
      path: `${path}.${key}`.slice(0, 220),
      key: String(key).slice(0, 80),
      value: value.slice(0, 160),
      score: Math.max(textScore, numericScore),
      text_score: textScore,
      numeric_score: numericScore,
      name: meta.name || "",
      point: meta.point || null
    });
  }

  function enqueueChildren(queue, obj, path, depth) {
    if (depth >= 7) return;
    for (const key of ownKeys(obj)) {
      let child;
      try { child = obj[key]; } catch (_) { continue; }
      if (child == null) continue;
      const type = typeof child;
      if (type === "object" || type === "function") {
        queue.push({ obj: child, path: `${path}.${key}`, depth: depth + 1 });
      }
    }
    for (const key of ["children", "_children", "nodes", "_nodes", "views", "components", "_components"]) {
      let list;
      try { list = obj[key]; } catch (_) { continue; }
      if (!Array.isArray(list)) continue;
      for (let i = 0; i < Math.min(list.length, 80); i += 1) {
        if (list[i]) queue.push({ obj: list[i], path: `${path}.${key}[${i}]`, depth: depth + 1 });
      }
    }
  }

  const roots = [];
  function addRoot(name, getter) {
    try {
      const obj = getter();
      if (obj) roots.push({ name, obj });
    } catch (_) {}
  }
  const rootNames = [
    "application", "cc", "Laya", "Game", "game", "gameData", "playerData",
    "windowManager", "sceneManager", "app", "__NUXT__", "__NEXT_DATA__"
  ];
  for (const name of rootNames) {
    try { if (window[name]) roots.push({ name, obj: window[name] }); } catch (_) {}
  }
  addRoot("cc.director.scene", () => window.cc && window.cc.director && window.cc.director.getScene && window.cc.director.getScene());
  addRoot("cc.director.runningScene", () => window.cc && window.cc.director && window.cc.director.getRunningScene && window.cc.director.getRunningScene());
  addRoot("cc.director.runningScene2", () => window.cc && window.cc.director && window.cc.director._runningScene);
  addRoot("Laya.stage", () => window.Laya && window.Laya.stage);
  addRoot("application.stage", () => window.application && window.application.stage);
  addRoot("application.root", () => window.application && (window.application.root || window.application._root || window.application._rootNode));
  addRoot("application.scene", () => window.application && (window.application.scene || window.application._scene || window.application.currentScene));
  addRoot("application.currentScene", () => window.application && window.application._currentScene);
  addRoot("app.stage", () => window.app && window.app.stage);
  addRoot("game.stage", () => window.game && window.game.stage);
  addRoot("Game.stage", () => window.Game && window.Game.stage);
  try {
    for (const key of Object.keys(window).slice(0, 300)) {
      if (/game|scene|room|table|desk|baccarat|bjl|manager|store|data|view|layer|ui/i.test(key)) {
        try { if (window[key]) roots.push({ name: `window.${key}`, obj: window[key] }); } catch (_) {}
      }
    }
  } catch (_) {}

  const matches = [];
  const seen = new WeakSet();
  const queue = roots.map((root) => ({ obj: root.obj, path: root.name, depth: 0 }));
  let visited = 0;
  while (queue.length && visited < 9000 && matches.length < 600) {
    const item = queue.shift();
    const obj = item.obj;
    if (!obj || (typeof obj !== "object" && typeof obj !== "function")) continue;
    if (seen.has(obj)) continue;
    seen.add(obj);
    visited += 1;
    const meta = { name: nameOf(obj), point: pointOf(obj) };
    for (const key of ownKeys(obj)) {
      let value;
      try { value = obj[key]; } catch (_) { continue; }
      if (typeof value === "string" || typeof value === "number") {
        addValue(matches, item.path, key, value, meta);
      }
    }
    enqueueChildren(queue, obj, item.path, item.depth);
  }

  matches.sort((a, b) => b.score - a.score);
  return {
    timestamp_ms: Date.now(),
    performance_ms: Math.round(performance.now()),
    url: String(location.href || ""),
    visited,
    matches: matches.slice(0, 80)
  };
}
"""


CANVAS_TEXT_PROBE_JS = r"""
(() => {
  if (window.__betDesktopCanvasTextProbeInstalled) return;
  window.__betDesktopCanvasTextProbeInstalled = true;
  const records = [];
  function push(kind, ctx, args) {
    try {
      const text = String(args[0] == null ? "" : args[0]).trim();
      if (!text) return;
      const canvas = ctx && ctx.canvas;
      const item = {
        ts: Date.now(),
        kind,
        text: text.slice(0, 160),
        x: Number(args[1]),
        y: Number(args[2]),
        font: String(ctx.font || "").slice(0, 120),
        width: canvas ? Number(canvas.width || 0) : 0,
        height: canvas ? Number(canvas.height || 0) : 0
      };
      records.push(item);
      if (records.length > 1500) records.splice(0, records.length - 1500);
    } catch (_) {}
  }
  function wrap(proto, name) {
    if (!proto || !proto[name] || proto[name].__betDesktopWrapped) return;
    const original = proto[name];
    const wrapped = function(...args) {
      push(name, this, args);
      return original.apply(this, args);
    };
    wrapped.__betDesktopWrapped = true;
    proto[name] = wrapped;
  }
  try {
    wrap(window.CanvasRenderingContext2D && window.CanvasRenderingContext2D.prototype, "fillText");
    wrap(window.CanvasRenderingContext2D && window.CanvasRenderingContext2D.prototype, "strokeText");
    wrap(window.OffscreenCanvasRenderingContext2D && window.OffscreenCanvasRenderingContext2D.prototype, "fillText");
    wrap(window.OffscreenCanvasRenderingContext2D && window.OffscreenCanvasRenderingContext2D.prototype, "strokeText");
  } catch (_) {}
  window.__betDesktopReadCanvasTextState = function() {
    const now = Date.now();
    return {
      timestamp_ms: now,
      url: String(location.href || ""),
      records: records.filter((item) => now - item.ts <= 3000).slice(-500)
    };
  };
})();
"""


ACTION_PHASES: dict[int, tuple[str, str, bool]] = {
    2: ("pre_bet", "waiting before betting", False),
    3: ("betting_open", "betting window open", True),
    5: ("settling", "payout or settling", False),
    9: ("pre_bet", "preparing next hand", False),
    10: ("dealing", "dealing cards", False),
    11: ("opening", "opening cards", False),
    12: ("opening", "opening cards", False),
    13: ("settling", "payout or settling", False),
}


@dataclass(frozen=True)
class RuntimeFrameState:
    page_index: int
    frame_index: int
    scene_name: str = ""
    scene_ready: bool = False
    selected_bet: int | None = None
    pending_chip_cents: int | None = None
    balance_cents: int | None = None
    balance_source: str = ""
    game_no: str = ""
    action: int | None = None
    timed: int | None = None
    current_load_type: int | None = None
    is_can_betting: bool | None = None
    is_unbet_count: int | None = None
    room_id: str = ""
    table_label: str = ""
    user_min_bet_cents: int | None = None
    user_max_bet_cents: int | None = None
    has_password_input: bool = False
    visible_input_count: int = 0
    balance_sources: list[dict[str, Any]] = field(default_factory=list)

    @property
    def balance_text(self) -> str:
        if self.balance_cents is None:
            return ""
        return f"{self.balance_cents / 100:.2f}"


@dataclass(frozen=True)
class RuntimeLabelFrameState:
    page_index: int
    frame_index: int
    game_no: str = ""
    countdown_seconds: int | None = None
    room_label: str = ""
    limit_label: str = ""
    phase_text: str = ""
    confidence: float = 0.0
    render_signal: bool = False
    visited: int = 0
    matches: list[dict[str, Any]] = field(default_factory=list)

    @property
    def has_state(self) -> bool:
        return any((self.game_no, self.countdown_seconds is not None, self.room_label, self.limit_label, self.phase_text))


@dataclass(frozen=True)
class LiveLabelRuntimeSnapshot:
    timestamp_ms: int
    instance_id: str
    frame: RuntimeLabelFrameState

    @property
    def game_no(self) -> str:
        return self.frame.game_no

    @property
    def countdown_seconds(self) -> int | None:
        return self.frame.countdown_seconds

    @property
    def room_label(self) -> str:
        return self.frame.room_label

    @property
    def limit_label(self) -> str:
        return self.frame.limit_label

    @property
    def phase_text(self) -> str:
        return self.frame.phase_text

    @property
    def confidence(self) -> float:
        return self.frame.confidence

    def to_safe_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["age_ms"] = max(0, now_ms() - self.timestamp_ms)
        return data


@dataclass(frozen=True)
class CanvasTextSnapshot:
    timestamp_ms: int
    instance_id: str
    game_no: str = ""
    countdown_seconds: int | None = None
    room_label: str = ""
    limit_label: str = ""
    phase_text: str = ""
    confidence: float = 0.0
    records: list[dict[str, Any]] = field(default_factory=list)

    @property
    def has_state(self) -> bool:
        return any((self.game_no, self.countdown_seconds is not None, self.room_label, self.limit_label, self.phase_text))

    def to_safe_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["age_ms"] = max(0, now_ms() - self.timestamp_ms)
        return data


@dataclass(frozen=True)
class LiveRuntimeSnapshot:
    timestamp_ms: int
    instance_id: str
    frame: RuntimeFrameState | None
    game_visible: bool
    layout_confidence: float
    viewport: dict[str, Any]
    phase_key: str
    phase_label: str
    betting_open: bool
    coordinates: dict[str, Any]

    @property
    def game_no(self) -> str:
        return self.frame.game_no if self.frame else ""

    @property
    def balance_text(self) -> str:
        return self.frame.balance_text if self.frame else ""

    @property
    def countdown_seconds(self) -> int | None:
        if self.frame is None or self.frame.timed is None:
            return None
        if 0 <= self.frame.timed <= 90:
            return self.frame.timed
        return None

    def to_safe_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if self.frame is not None:
            data["balance_text"] = self.frame.balance_text
        data["age_ms"] = max(0, now_ms() - self.timestamp_ms)
        return data


async def read_live_runtime_snapshot(
    page: Any,
    *,
    instance_id: str = "",
    screenshot_png: bytes | None = None,
    include_geometry: bool = True,
) -> LiveRuntimeSnapshot | None:
    """Read the best live runtime state from the page and current screenshot."""

    pages = _context_pages(page)
    frames = await _scan_pages(pages)
    best_frame = _best_frame(frames)
    geometry_page = pages[best_frame.page_index] if best_frame is not None and best_frame.page_index < len(pages) else page
    if include_geometry:
        viewport, game_visible, confidence, coordinates = await _read_geometry(
            geometry_page,
            screenshot_png,
            instance_id=instance_id,
        )
    else:
        viewport, game_visible, confidence, coordinates = {}, False, 0.0, {}
    if best_frame is None and not game_visible:
        return None

    phase_key = "unknown"
    phase_label = "waiting for game state"
    betting_open = False
    if best_frame is not None:
        if best_frame.action is not None:
            phase_key, phase_label, betting_open = ACTION_PHASES.get(
                int(best_frame.action),
                ("unknown", f"unknown action={best_frame.action}", False),
            )
        elif best_frame.current_load_type is not None:
            phase_key, phase_label, betting_open = ACTION_PHASES.get(
                int(best_frame.current_load_type),
                (f"load_type_{best_frame.current_load_type}", f"currentLoadType={best_frame.current_load_type}", False),
            )
        elif best_frame.is_can_betting is not None:
            betting_open = bool(best_frame.is_can_betting)
            phase_key = "betting_open" if betting_open else "betting_closed"
            phase_label = "betting window open" if betting_open else "betting window closed"
        if betting_open and best_frame.timed is not None and best_frame.timed <= 0:
            betting_open = False
            phase_label = "betting window closing"

    return LiveRuntimeSnapshot(
        timestamp_ms=now_ms(),
        instance_id=instance_id,
        frame=best_frame,
        game_visible=game_visible,
        layout_confidence=confidence,
        viewport=viewport,
        phase_key=phase_key,
        phase_label=phase_label,
        betting_open=betting_open,
        coordinates=coordinates,
    )


async def read_live_label_runtime_snapshot(
    page: Any,
    *,
    instance_id: str = "",
) -> LiveLabelRuntimeSnapshot | None:
    """Read visible game labels from browser runtime objects.

    This is separate from ``read_live_runtime_snapshot`` because the old
    ``application._currentScene`` object can lag until a user action refreshes
    it. Label objects are closer to the canvas text currently being rendered.
    """

    pages = _context_pages(page)
    frames: list[RuntimeLabelFrameState] = []
    for page_index, candidate in enumerate(pages):
        frames.extend(await _scan_label_frames(candidate, page_index=page_index))
    best_frame = _best_label_frame(frames)
    if best_frame is None:
        return None
    return LiveLabelRuntimeSnapshot(
        timestamp_ms=now_ms(),
        instance_id=instance_id,
        frame=best_frame,
    )


async def install_canvas_text_probe(context: Any) -> None:
    try:
        await context.add_init_script(CANVAS_TEXT_PROBE_JS)
    except Exception:
        return


async def evaluate_canvas_text_probe(page: Any) -> None:
    try:
        await page.evaluate(CANVAS_TEXT_PROBE_JS)
    except Exception:
        return


async def read_canvas_text_snapshot(page: Any, *, instance_id: str = "") -> CanvasTextSnapshot | None:
    pages = _context_pages(page)
    snapshots: list[CanvasTextSnapshot] = []
    for candidate in pages:
        await evaluate_canvas_text_probe(candidate)
        try:
            frames = list(candidate.frames)
        except Exception:
            frames = []
        for frame in frames:
            try:
                await frame.evaluate(CANVAS_TEXT_PROBE_JS)
                raw = await frame.evaluate(
                    "() => window.__betDesktopReadCanvasTextState ? window.__betDesktopReadCanvasTextState() : null"
                )
            except Exception:
                continue
            if isinstance(raw, dict):
                snapshot = _canvas_text_snapshot_from_raw(raw, instance_id=instance_id)
                if snapshot.has_state:
                    snapshots.append(snapshot)
    if not snapshots:
        return None
    return sorted(snapshots, key=lambda item: item.confidence, reverse=True)[0]


def _context_pages(page: Any) -> list[Any]:
    try:
        context = page.context
        pages = [item for item in list(context.pages) if item is not None]
    except Exception:
        pages = []
    if not pages:
        pages = [page]
    return pages


async def _scan_pages(pages: list[Any]) -> list[RuntimeFrameState]:
    states: list[RuntimeFrameState] = []
    for page_index, candidate in enumerate(pages):
        states.extend(await _scan_frames(candidate, page_index=page_index))
    return states


async def _scan_frames(page: Any, *, page_index: int) -> list[RuntimeFrameState]:
    states: list[RuntimeFrameState] = []
    try:
        frames = list(page.frames)
    except Exception:
        return states
    for frame_index, frame in enumerate(frames):
        try:
            raw = await frame.evaluate(RUNTIME_STATE_JS)
        except Exception:
            continue
        if not isinstance(raw, dict):
            continue
        state = _frame_state_from_raw(raw, page_index=page_index, frame_index=frame_index)
        if _frame_has_signal(state):
            states.append(state)
    return states


async def _scan_label_frames(page: Any, *, page_index: int) -> list[RuntimeLabelFrameState]:
    states: list[RuntimeLabelFrameState] = []
    try:
        frames = list(page.frames)
    except Exception:
        return states
    for frame_index, frame in enumerate(frames):
        try:
            raw = await frame.evaluate(LABEL_RUNTIME_STATE_JS)
        except Exception:
            continue
        if not isinstance(raw, dict):
            continue
        state = _label_frame_state_from_raw(raw, page_index=page_index, frame_index=frame_index)
        if state.has_state:
            states.append(state)
    return states


def _frame_state_from_raw(raw: dict[str, Any], *, page_index: int, frame_index: int) -> RuntimeFrameState:
    return RuntimeFrameState(
        page_index=page_index,
        frame_index=frame_index,
        scene_name=str(raw.get("scene_name") or "")[:120],
        scene_ready=bool(raw.get("scene_ready")),
        selected_bet=_as_int_or_none(raw.get("selected_bet")),
        pending_chip_cents=_as_int_or_none(raw.get("pending_chip_cents")),
        balance_cents=_as_int_or_none(raw.get("balance_cents")),
        balance_source=str(raw.get("balance_source") or "")[:120],
        game_no=str(raw.get("game_no") or "")[:120],
        action=_as_int_or_none(raw.get("action")),
        timed=_as_int_or_none(raw.get("timed")),
        current_load_type=_as_int_or_none(raw.get("current_load_type")),
        is_can_betting=_as_bool_or_none(raw.get("is_can_betting")),
        is_unbet_count=_as_int_or_none(raw.get("is_unbet_count")),
        room_id=str(raw.get("room_id") or "")[:40],
        table_label=_normalize_table_label(raw.get("table_label")),
        user_min_bet_cents=_as_int_or_none(raw.get("user_min_bet_cents")),
        user_max_bet_cents=_as_int_or_none(raw.get("user_max_bet_cents")),
        has_password_input=bool(raw.get("has_password_input")),
        visible_input_count=int(raw.get("visible_input_count") or 0),
        balance_sources=[
            {
                "name": str(item.get("name", ""))[:80],
                "path": str(item.get("path", ""))[:180],
                "raw": str(item.get("raw", ""))[:80],
                "cents": _as_int_or_none(item.get("cents")),
            }
            for item in (raw.get("balance_sources") or [])
            if isinstance(item, dict)
        ],
    )


_FULL_BATCH_RE = re.compile(r"(?<![\d-])\d{2,}-\d{6,}-\d{6,}-\d+(?!\d)")
_PHASE_RE = re.compile(
    r"(\u51c6\u5907\u4e2d|\u4e0b\u6ce8\u4e2d|\u5f00\u724c\u4e2d|\u53d1\u724c\u4e2d|\u4eae\u724c\u4e2d|"
    r"\u7ed3\u7b97\u4e2d|\u6d3e\u5f69\u4e2d|\u7b49\u5f85\u4e2d|\u4f11\u606f\u4e2d|\u5c01\u76d8|\u505c\u6b62\u4e0b\u6ce8)"
)
_ROOM_WITH_LABEL_RE = re.compile(r"(?:\u623f\u95f4\u53f7|\u623f\u53f7)\s*([A-Z]\d{3,})", re.IGNORECASE)
_ROOM_TABLE_RE = re.compile(r"\b(T\d{3,})\b", re.IGNORECASE)
_LIMIT_WITH_LABEL_RE = re.compile(
    r"(?:\u9650\u7ea2|\u9650\u989d)\s*(\d{1,5}(?:\.\d+)?\s*[-~]\s*\d{1,5}(?:\.\d+)?)"
)
_LIMIT_RANGE_RE = re.compile(r"(?<![\d-])(\d{1,5}(?:\.\d+)?\s*[-~]\s*\d{1,5}(?:\.\d+)?)(?![\d-])")
_LABEL_COUNTDOWN_BAD_PATH_RE = re.compile(
    r"history|record|road|trend|result|list|chip|chouma|betarea|score|gold|money|balance|wallet|"
    r"player|user|card|poker|point|endtime|masktimer|analyticsdata|_prevscene|btnchangescreen",
    re.IGNORECASE,
)
_LABEL_COUNTDOWN_RENDER_PATH_RE = re.compile(
    r"cc\.director|Laya\.stage|\.stage|children|_children|components|_components",
    re.IGNORECASE,
)
_LABEL_COUNTDOWN_HINT_RE = re.compile(
    r"time|timed|timer|countdown|left|remain|second|clock|cd",
    re.IGNORECASE,
)


def _label_frame_state_from_raw(raw: dict[str, Any], *, page_index: int, frame_index: int) -> RuntimeLabelFrameState:
    matches = [
        {
            "path": str(item.get("path", ""))[:220],
            "key": str(item.get("key", ""))[:80],
            "value": str(item.get("value", ""))[:160],
            "score": _as_float(item.get("score")),
            "text_score": _as_float(item.get("text_score")),
            "numeric_score": _as_float(item.get("numeric_score")),
            "name": str(item.get("name", ""))[:80],
            "point": item.get("point") if isinstance(item.get("point"), dict) else None,
        }
        for item in (raw.get("matches") or [])
        if isinstance(item, dict)
    ]
    game_no = _pick_label_regex(matches, _FULL_BATCH_RE)
    room_label = _pick_room_label(matches)
    limit_label = _pick_limit_label(matches).replace(" ", "")
    phase_text = _pick_label_regex(matches, _PHASE_RE, group=1)
    countdown = _pick_label_countdown(matches)
    render_signal = _has_render_signal(matches)
    confidence = _label_confidence(
        game_no=game_no,
        countdown=countdown,
        room_label=room_label,
        limit_label=limit_label,
        phase_text=phase_text,
        render_signal=render_signal,
    )
    return RuntimeLabelFrameState(
        page_index=page_index,
        frame_index=frame_index,
        game_no=game_no[:120],
        countdown_seconds=countdown,
        room_label=room_label[:40],
        limit_label=limit_label[:40],
        phase_text=phase_text[:40],
        confidence=confidence,
        render_signal=render_signal,
        visited=_as_int_or_none(raw.get("visited")) or 0,
        matches=matches[:20],
    )


def _pick_label_regex(matches: list[dict[str, Any]], regex: re.Pattern[str], *, group: int = 0) -> str:
    selected: tuple[float, int, str] | None = None
    for index, item in enumerate(matches):
        value = str(item.get("value") or "")
        found = regex.search(value)
        if not found:
            continue
        extracted = found.group(group).strip()
        score = float(item.get("score") or 0)
        candidate = (score, -index, extracted)
        if selected is None or candidate > selected:
            selected = candidate
    return selected[2] if selected else ""


def _pick_limit_label(matches: list[dict[str, Any]]) -> str:
    labelled = _pick_label_regex(matches, _LIMIT_WITH_LABEL_RE, group=1)
    if labelled:
        return labelled
    selected: tuple[float, int, str] | None = None
    for index, item in enumerate(matches):
        path = f"{item.get('path', '')}.{item.get('name', '')}.{item.get('key', '')}"
        if not re.search(r"limit|red|\u9650\u7ea2|\u9650\u989d", path, re.IGNORECASE):
            continue
        value = str(item.get("value") or "")
        found = _LIMIT_RANGE_RE.search(value)
        if not found:
            continue
        candidate = (float(item.get("score") or 0), -index, found.group(1).strip())
        if selected is None or candidate > selected:
            selected = candidate
    return selected[2] if selected else ""


def _pick_room_label(matches: list[dict[str, Any]]) -> str:
    labelled = _pick_label_regex(matches, _ROOM_WITH_LABEL_RE, group=1)
    if labelled:
        return labelled
    selected: tuple[float, int, str] | None = None
    for index, item in enumerate(matches):
        value = str(item.get("value") or "")
        found = _ROOM_TABLE_RE.search(value)
        if not found:
            continue
        path = f"{item.get('path', '')}.{item.get('name', '')}.{item.get('key', '')}"
        if not re.search(r"room|table|desk|\u623f|\u53f7", path, re.IGNORECASE):
            continue
        candidate = (float(item.get("score") or 0), -index, found.group(1).upper())
        if selected is None or candidate > selected:
            selected = candidate
    return selected[2] if selected else ""


def _pick_label_countdown(matches: list[dict[str, Any]]) -> int | None:
    selected: tuple[float, int, int] | None = None
    for index, item in enumerate(matches):
        value = str(item.get("value") or "").strip()
        if not re.fullmatch(r"\d{1,2}", value):
            continue
        number = int(value)
        if not 0 <= number <= 90:
            continue
        path = f"{item.get('path', '')}.{item.get('name', '')}.{item.get('key', '')}"
        if _LABEL_COUNTDOWN_BAD_PATH_RE.search(path):
            continue
        numeric_score = float(item.get("numeric_score") or 0)
        if numeric_score < 40:
            continue
        has_render_path = bool(_LABEL_COUNTDOWN_RENDER_PATH_RE.search(path))
        has_countdown_hint = bool(_LABEL_COUNTDOWN_HINT_RE.search(path))
        if not (has_render_path or has_countdown_hint):
            continue
        if number == 0 and not has_render_path:
            continue
        candidate = (numeric_score, -index, number)
        if selected is None or candidate > selected:
            selected = candidate
    return selected[2] if selected else None


def _has_render_signal(matches: list[dict[str, Any]]) -> bool:
    render_path = re.compile(r"cc\.director|Laya\.stage|\.stage|children|_children|components|_components", re.I)
    for item in matches[:20]:
        path = f"{item.get('path', '')}.{item.get('name', '')}.{item.get('key', '')}"
        if not render_path.search(path):
            continue
        value = str(item.get("value") or "")
        if _FULL_BATCH_RE.search(value) or _PHASE_RE.search(value) or re.fullmatch(r"\d{1,2}", value):
            return True
    return False


def _label_confidence(
    *,
    game_no: str,
    countdown: int | None,
    room_label: str,
    limit_label: str,
    phase_text: str,
    render_signal: bool,
) -> float:
    score = 0.0
    if game_no:
        score += 0.42
    if phase_text:
        score += 0.22
    if countdown is not None:
        score += 0.16
    if room_label:
        score += 0.10
    if limit_label:
        score += 0.10
    if not render_signal and not phase_text:
        score = min(score, 0.30)
    return min(1.0, score)


def _frame_has_signal(state: RuntimeFrameState) -> bool:
    return any(
        (
            state.scene_ready,
            state.balance_cents is not None,
            bool(state.game_no),
            state.action is not None,
            state.timed is not None,
            state.current_load_type is not None,
            state.is_can_betting is not None,
            state.has_password_input,
        )
    )


def _best_label_frame(states: list[RuntimeLabelFrameState]) -> RuntimeLabelFrameState | None:
    if not states:
        return None
    return sorted(
        states,
        key=lambda item: (
            item.confidence,
            bool(item.game_no),
            bool(item.phase_text),
            item.countdown_seconds is not None,
            bool(item.room_label),
            bool(item.limit_label),
            -item.frame_index,
        ),
        reverse=True,
    )[0]


def _canvas_text_snapshot_from_raw(raw: dict[str, Any], *, instance_id: str) -> CanvasTextSnapshot:
    records = [
        {
            "text": str(item.get("text") or "")[:160],
            "x": _as_float(item.get("x")),
            "y": _as_float(item.get("y")),
            "width": _as_float(item.get("width")),
            "height": _as_float(item.get("height")),
            "font": str(item.get("font") or "")[:120],
            "age_ms": max(0, (_as_int_or_none(raw.get("timestamp_ms")) or 0) - (_as_int_or_none(item.get("ts")) or 0)),
        }
        for item in (raw.get("records") or [])
        if isinstance(item, dict) and str(item.get("text") or "").strip()
    ]
    game_no = _pick_canvas_regex(records, _FULL_BATCH_RE)
    room_label = _pick_canvas_room(records)
    limit_label = _pick_canvas_limit(records)
    phase_text = _pick_canvas_phase(records)
    countdown = _pick_canvas_countdown(records)
    confidence = _canvas_confidence(
        game_no=game_no,
        countdown=countdown,
        room_label=room_label,
        limit_label=limit_label,
        phase_text=phase_text,
    )
    useful_records = _useful_canvas_records(records)
    return CanvasTextSnapshot(
        timestamp_ms=now_ms(),
        instance_id=instance_id,
        game_no=game_no[:120],
        countdown_seconds=countdown,
        room_label=room_label[:40],
        limit_label=limit_label[:40],
        phase_text=phase_text[:40],
        confidence=confidence,
        records=useful_records[:24],
    )


def _pick_canvas_regex(records: list[dict[str, Any]], regex: re.Pattern[str], *, group: int = 0) -> str:
    selected: tuple[float, int, str] | None = None
    for index, item in enumerate(records):
        text = str(item.get("text") or "")
        found = regex.search(text)
        if not found:
            continue
        score = _canvas_record_position_score(item)
        candidate = (score, -index, found.group(group).strip())
        if selected is None or candidate > selected:
            selected = candidate
    return selected[2] if selected else ""


def _pick_canvas_room(records: list[dict[str, Any]]) -> str:
    labelled = _pick_canvas_regex(records, _ROOM_WITH_LABEL_RE, group=1)
    if labelled:
        return labelled.upper()
    return ""


def _pick_canvas_limit(records: list[dict[str, Any]]) -> str:
    labelled = _pick_canvas_regex(records, _LIMIT_WITH_LABEL_RE, group=1)
    if labelled:
        return labelled.replace(" ", "")
    return ""


def _pick_canvas_phase(records: list[dict[str, Any]]) -> str:
    return _pick_canvas_regex(records, _PHASE_RE, group=1)


def _pick_canvas_countdown(records: list[dict[str, Any]]) -> int | None:
    selected: tuple[float, int, int] | None = None
    for index, item in enumerate(records):
        text = str(item.get("text") or "").strip()
        if not re.fullmatch(r"\d{1,2}", text):
            continue
        value = int(text)
        if not 0 <= value <= 20:
            continue
        score = _canvas_countdown_score(item)
        if score < 70:
            continue
        if value == 0 and score < 90:
            continue
        candidate = (score, -index, value)
        if selected is None or candidate > selected:
            selected = candidate
    return selected[2] if selected else None


def _canvas_record_position_score(item: dict[str, Any]) -> float:
    width = float(item.get("width") or 0)
    height = float(item.get("height") or 0)
    x = float(item.get("x") or 0)
    y = float(item.get("y") or 0)
    score = 50.0
    if width > 0 and height > 0:
        if y <= height * 0.22:
            score += 30
        if x <= width * 0.45:
            score += 10
    return score


def _canvas_countdown_score(item: dict[str, Any]) -> float:
    text = str(item.get("text") or "")
    width = float(item.get("width") or 0)
    height = float(item.get("height") or 0)
    x = float(item.get("x") or 0)
    y = float(item.get("y") or 0)
    score = 0.0
    if width > 0 and height > 0:
        if height * 0.05 <= y <= height * 0.28:
            score += 55
        if width * 0.45 <= x <= width * 0.85:
            score += 45
        if y >= height * 0.55:
            score -= 80
    if text in {"10", "20", "50", "100", "200"}:
        score -= 45
    return score


def _canvas_confidence(
    *,
    game_no: str,
    countdown: int | None,
    room_label: str,
    limit_label: str,
    phase_text: str,
) -> float:
    score = 0.0
    if game_no:
        score += 0.40
    if phase_text:
        score += 0.25
    if countdown is not None:
        score += 0.15
    if room_label:
        score += 0.10
    if limit_label:
        score += 0.10
    return min(1.0, score)


def _useful_canvas_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    useful: list[dict[str, Any]] = []
    for item in records[-200:]:
        text = str(item.get("text") or "")
        if (
            _FULL_BATCH_RE.search(text)
            or _ROOM_WITH_LABEL_RE.search(text)
            or _LIMIT_WITH_LABEL_RE.search(text)
            or _PHASE_RE.search(text)
            or re.fullmatch(r"\d{1,2}", text)
        ):
            useful.append(item)
    return useful[-40:]


def _best_frame(states: list[RuntimeFrameState]) -> RuntimeFrameState | None:
    if not states:
        return None
    return sorted(
        states,
        key=lambda item: (
            item.scene_ready,
            bool(item.game_no),
            item.balance_cents is not None,
            item.action is not None,
            item.is_can_betting is not None,
            item.current_load_type is not None,
            -item.frame_index,
        ),
        reverse=True,
    )[0]


async def _read_geometry(
    page: Any,
    screenshot_png: bytes | None,
    *,
    instance_id: str = "",
) -> tuple[dict[str, Any], bool, float, dict[str, Any]]:
    png = screenshot_png
    width = 0
    height = 0
    game_visible = False
    confidence = 0.0
    if png:
        try:
            image = Image.open(io.BytesIO(png))
            width, height = image.size
            visual = recognize_live_game_layout_from_png(png)
            game_visible = visual.game_visible
            confidence = float(visual.confidence)
        except Exception:
            width = height = 0

    if width <= 0 or height <= 0:
        try:
            viewport = page.viewport_size or {}
            width = int(viewport.get("width") or 0)
            height = int(viewport.get("height") or 0)
        except Exception:
            width = height = 0

    canvas_geometry = await _read_canvas_runtime_geometry(page)
    if canvas_geometry:
        runtime_viewport = canvas_geometry.get("viewport") if isinstance(canvas_geometry.get("viewport"), dict) else {}
        if width <= 0:
            width = int(runtime_viewport.get("width") or 0)
        if height <= 0:
            height = int(runtime_viewport.get("height") or 0)

    viewport_payload = {"width": width, "height": height}
    if canvas_geometry:
        canvas_rect = canvas_geometry.get("canvas_rect") if isinstance(canvas_geometry.get("canvas_rect"), dict) else {}
        if canvas_rect:
            viewport_payload["canvas_rect"] = canvas_rect
        stage = canvas_geometry.get("stage") if isinstance(canvas_geometry.get("stage"), dict) else {}
        if stage:
            viewport_payload["stage"] = stage
        coordinates = _coordinates_for_viewport(
            width,
            height,
            instance_id=instance_id,
            canvas_rect=canvas_rect,
        )
        calibration = coordinates.setdefault("calibration", {})
        if isinstance(calibration, dict):
            calibration["geometry_source"] = "canvas_dom"
            if stage:
                calibration["stage"] = stage
            canvas_attr = canvas_geometry.get("canvas_attr")
            if isinstance(canvas_attr, dict):
                calibration["canvas_attr"] = canvas_attr
    else:
        coordinates = _coordinates_for_viewport(width, height, instance_id=instance_id) if width > 0 and height > 0 else {}
    return viewport_payload, game_visible, confidence, coordinates


async def _read_canvas_runtime_geometry(page: Any) -> dict[str, Any] | None:
    try:
        frames = list(page.frames)
    except Exception:
        frames = []
    for frame_index, frame in enumerate(frames):
        try:
            raw = await frame.evaluate(CANVAS_GEOMETRY_JS)
        except Exception:
            continue
        if not isinstance(raw, dict):
            continue
        rect = raw.get("canvas_rect")
        if not isinstance(rect, dict):
            continue
        try:
            width = float(rect.get("width") or 0.0)
            height = float(rect.get("height") or 0.0)
            if width <= 0 or height <= 0:
                continue
            offset_x, offset_y = await _frame_page_offset(frame, page)
            raw["frame_index"] = frame_index
            raw["canvas_rect"] = {
                "x": round(max(0.0, offset_x + float(rect.get("x") or 0.0)), 2),
                "y": round(max(0.0, offset_y + float(rect.get("y") or 0.0)), 2),
                "width": round(width, 2),
                "height": round(height, 2),
            }
            return raw
        except Exception:
            continue
    return None


async def _frame_page_offset(frame: Any, page: Any) -> tuple[float, float]:
    try:
        if frame == page.main_frame:
            return 0.0, 0.0
    except Exception:
        pass
    try:
        element = await frame.frame_element()
        box = await element.bounding_box()
        if box:
            return float(box.get("x") or 0.0), float(box.get("y") or 0.0)
    except Exception:
        pass
    return 0.0, 0.0


def _coordinates_for_viewport(
    width: int,
    height: int,
    *,
    instance_id: str = "",
    canvas_rect: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return coordinates_from_runtime_geometry(
        viewport_width=width,
        viewport_height=height,
        instance_id=instance_id,
        canvas_rect=canvas_rect,
    )


def _as_int_or_none(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float:
    try:
        if value is None or value == "":
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _normalize_table_label(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if re.fullmatch(r"T\d{3,}", text, re.I):
        return text.upper()[:20]
    if re.fullmatch(r"\d{1,6}", text):
        return f"T{int(text[-3:]):03d}"
    return ""


def _as_bool_or_none(value: Any) -> bool | None:
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

