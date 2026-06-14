"""PyQt6 desktop dashboard for the bet_desktop shadow audit workflow.

This module is intentionally fail-closed. The "simulation fire" control runs
the same PlaywrightLiveDriver boundary used by production integration tests,
but that driver raises LiveDriverBlocked before any physical click can be sent.
"""

from __future__ import annotations

import asyncio
import base64
import csv
import json
import os
import random
import re
import sys
import time
import traceback
from queue import Empty, Queue
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PyQt6.QtCore import QRegularExpression, QTimer, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QMenu,
    QPushButton,
    QSpinBox,
    QSizePolicy,
    QStackedWidget,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from bet_desktop.bridge.live_execution_bridge import LiveExecutionBridge
from bet_desktop.coordinator.live_executor import LiveEnvironmentExecutor, LiveExecutorConfig
from bet_desktop.backend.cluster_process_worker import ClusterProcessController, ClusterWorkerConfig
from bet_desktop.browser.session_manager import (
    AccountConfig,
    BrowserInstanceConfig,
    BrowserSessionManager,
    BrowserSnapshot,
    ProxyConfig,
)
from bet_desktop.core.decomposition import decompose_value
from bet_desktop.core.adapters.canvas import CanvasBetAdapter
from bet_desktop.core.group_action import CanvasActionInstance
from bet_desktop.coordinator.hedge_execution_orchestrator import HedgeExecutionOrchestrator
from bet_desktop.core.execution_events import ExecutionEvent, ExecutionEventType
from bet_desktop.core.hedge_execution_config import AddonFourFrequency, AddonFourMode, HedgeExecutionConfig
from bet_desktop.core.exceptions import AutomationException
from bet_desktop.core.hedge_plan import HedgePlanPolicy, RandomHedgePlanGenerator
from bet_desktop.core.page_state import PageStateAssertor, PageStateSnapshot
from bet_desktop.core.runtime_bet_ledger import RuntimeBetLedger
from bet_desktop.drivers.playwright_live_driver import LiveDriverBlocked, PlaywrightLiveDriver
from bet_desktop.models.state_temporal_guard import TemporalStateSnapshot, now_ms
from bet_desktop.models.transaction_manifest import (
    ClickInstruction,
    HedgeExpectation,
    ShadowTransactionManifest,
)

INSTANCE_IDS = ("a1", "a2", "a3", "a4")
DENOMINATIONS = (4, 10, 20, 50, 100)
DEFAULT_RUNTIME_PIPELINE = "v2_shadow"
RUNTIME_V2_SHADOW_LOG_DIR = PROJECT_ROOT / "live_logs"
ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


def _debug_port_for_instance(instance_id: str) -> int:
    match = re.fullmatch(r"a(\d+)", str(instance_id or "").strip(), flags=re.IGNORECASE)
    if not match:
        return 0
    return 9332 + int(match.group(1))


def _normalize_navigation_url(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if re.match(r"^[a-z][a-z0-9+.-]*:", text, flags=re.IGNORECASE):
        return text
    return f"https://{text}"


def _stable_ui_batch_id(value: object) -> str:
    text = str(value or "").strip()
    if text in ("", "-"):
        return ""
    parts = text.split("-")
    if (
        len(parts) >= 4
        and len(parts[0]) >= 2
        and len(parts[1]) >= 6
        and len(parts[2]) >= 6
        and all(part.isdigit() for part in parts[:4])
    ):
        return "-".join(parts[:3])
    return text


@dataclass(frozen=True)
class InstanceTelemetry:
    instance_id: str
    role: str
    status: str
    batch_id: str
    countdown_seconds: int
    balance: float
    proxy_latency_ms: int
    proxy_state: str
    fingerprint_state: str


@dataclass
class DashboardRuntimeConfig:
    base_amount: int = 100
    hedge_model: str = "选项A：主闲 / 副庄对冲"
    fingerprint_version: int = 1
    proxy_snapshot: dict[str, dict[str, str]] | None = None
    account_snapshot: dict[str, dict[str, str]] | None = None


class JsonHighlighter(QSyntaxHighlighter):
    """Small JSON highlighter for the shadow instruction terminal."""

    def __init__(self, parent) -> None:
        super().__init__(parent)
        self._rules: list[tuple[QRegularExpression, QTextCharFormat]] = []
        self._add_rule(r'"[^"\\]*(\\.[^"\\]*)*"(?=\s*:)', "#7dd3fc", bold=True)
        self._add_rule(r':\s*"[^"\\]*(\\.[^"\\]*)*"', "#86efac")
        self._add_rule(r"\b-?\d+(?:\.\d+)?\b", "#fde68a")
        self._add_rule(r"\b(true|false|null)\b", "#fca5a5")

    def _add_rule(self, pattern: str, color: str, *, bold: bool = False) -> None:
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))
        if bold:
            fmt.setFontWeight(QFont.Weight.Bold)
        self._rules.append((QRegularExpression(pattern), fmt))

    def highlightBlock(self, text: str) -> None:
        for expression, fmt in self._rules:
            iterator = expression.globalMatch(text)
            while iterator.hasNext():
                match = iterator.next()
                self.setFormat(match.capturedStart(), match.capturedLength(), fmt)


class PillLabel(QLabel):
    def __init__(self, text: str, tone: str = "neutral") -> None:
        super().__init__(text)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setProperty("tone", tone)
        self.setMinimumHeight(28)


class StatusDot(QLabel):
    COLORS = {
        "ready": "#34d399",
        "blocked": "#fbbf24",
        "error": "#f87171",
    }

    def __init__(self) -> None:
        super().__init__("●")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFont(QFont("Arial", 22, QFont.Weight.Bold))
        self.set_state("ready")

    def set_state(self, state: str) -> None:
        self.setStyleSheet(f"color: {self.COLORS.get(state, '#94a3b8')};")


class GlobalAuditorPanel(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("globalAuditorPanel")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(12)

        title = QLabel("全局审计看板")
        title.setObjectName("panelTitle")
        title.setMinimumWidth(170)

        self.mode_label = PillLabel("影子审计模式", "warning")
        self.mode_label.setObjectName("global_mode")

        self.equation_label = QLabel("主号余额变化 + 副号余额变化合计 = 0")
        self.equation_label.setObjectName("hedge_equation")

        self.delta_label = PillLabel("等待真实余额断言", "warning")
        self.delta_label.setObjectName("hedge_delta_assertion")

        self.proxy_label = PillLabel("代理等待真实检测", "warning")
        self.fp_label = PillLabel("指纹等待真实检测", "warning")
        self.live_label = PillLabel("真实执行已阻断", "danger")

        layout.addWidget(title)
        layout.addWidget(self.mode_label)
        layout.addWidget(self.equation_label, 1)
        layout.addWidget(self.delta_label)
        layout.addWidget(self.proxy_label)
        layout.addWidget(self.fp_label)
        layout.addWidget(self.live_label)

    def update_delta(self, value: float) -> None:
        ok = abs(value) < 0.0001
        self.delta_label.setText(f"{'断言通过' if ok else '断言失败'} · 差值 {value:.2f}")
        self.delta_label.setProperty("tone", "ok" if ok else "danger")
        self.delta_label.style().unpolish(self.delta_label)
        self.delta_label.style().polish(self.delta_label)

    def update_runtime_summary(self, snapshots: dict[str, BrowserSnapshot]) -> None:
        total = len(snapshots)
        if total == 0:
            self.proxy_label.setText("代理等待真实检测")
            self.proxy_label.setProperty("tone", "warning")
            self.fp_label.setText("指纹等待真实检测")
            self.fp_label.setProperty("tone", "warning")
        else:
            proxy_ok = sum("可达" in item.proxy_state for item in snapshots.values())
            self.proxy_label.setText(f"代理/网络 {proxy_ok}/{total} 可达")
            self.proxy_label.setProperty("tone", "ok" if proxy_ok == total else "danger")
            fp_detected = sum("摘要" in item.fingerprint_state for item in snapshots.values())
            self.fp_label.setText(f"指纹摘要 {fp_detected}/{total} 已采集")
            self.fp_label.setProperty("tone", "ok" if fp_detected == total else "warning")
        for label in (self.proxy_label, self.fp_label):
            label.style().unpolish(label)
            label.style().polish(label)


    def update_temporal_summary(self, snapshots: dict[str, TemporalStateSnapshot]) -> None:
        total = len(snapshots)
        if total == 0:
            self.proxy_label.setText("WS 状态源等待连接")
            self.proxy_label.setProperty("tone", "warning")
            self.fp_label.setText("页面状态源等待")
            self.fp_label.setProperty("tone", "warning")
        else:
            current = now_ms()
            fresh_count = 0
            connected_count = 0
            for item in snapshots.values():
                age = item.age_ms(current_ms=current)
                if item.ws_connected:
                    connected_count += 1
                if item.ws_connected and item.page_alive and age <= 300:
                    fresh_count += 1
            self.proxy_label.setText(f"WS 心跳 {fresh_count}/{total} 实时")
            self.proxy_label.setProperty("tone", "ok" if fresh_count == total else "danger")
            self.fp_label.setText(f"页面状态源 · WS连接 {connected_count}/{total}")
            self.fp_label.setProperty("tone", "ok" if connected_count == total else "warning")
        for label in (self.proxy_label, self.fp_label):
            label.style().unpolish(label)
            label.style().polish(label)


class InstanceCard(QFrame):
    LIVE_TEMPORAL_DISPLAY_TTL_MS = 5000
    SHADOW_DISPLAY_TTL_MS = 5000

    def __init__(self, instance_id: str) -> None:
        super().__init__()
        self.instance_id = instance_id
        self._last_temporal_update_ms = 0
        self._shadow_display: dict[str, Any] = {}
        self._shadow_countdown_anchor: int | None = None
        self._shadow_countdown_anchor_ms = 0
        self._shadow_countdown_batch_key = ""
        self._shadow_countdown_phase_key = ""
        self.setObjectName(f"{instance_id}_instance_card")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(10)

        header = QHBoxLayout()
        self.dot = StatusDot()
        self.title_label = QLabel(instance_id)
        self.title_label.setObjectName(f"{instance_id}_role")
        self.title_label.setFont(QFont("Microsoft YaHei", 13, QFont.Weight.Bold))
        self.status_label = QLabel("就绪")
        self.status_label.setObjectName(f"{instance_id}_status")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.ws_heartbeat = QLabel("WS 未连接")
        self.ws_heartbeat.setObjectName(f"{instance_id}_ws_heartbeat")
        self.ws_heartbeat.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.ws_heartbeat.setMinimumWidth(150)
        self._set_ws_heartbeat(-1)
        header.addWidget(self.dot)
        header.addWidget(self.title_label)
        header.addWidget(self.ws_heartbeat)
        header.addWidget(self.status_label, 1)
        root.addLayout(header)

        matrix = QGridLayout()
        matrix.setHorizontalSpacing(10)
        matrix.setVerticalSpacing(8)
        self.batch_id = self._value_label(f"{instance_id}_batch_id")
        self.countdown = self._value_label(f"{instance_id}_countdown")
        self.balance = self._value_label(f"{instance_id}_ocr_balance")
        self.proxy = self._value_label(f"{instance_id}_proxy_state")
        self.fingerprint = self._value_label(f"{instance_id}_fingerprint_state")
        self.proxy.hide()
        self.fingerprint.hide()
        self.game_no = self._value_label(f"{instance_id}_game_no")
        self.runtime_phase = self._value_label(f"{instance_id}_runtime_phase")
        self.runtime_coordinates = self._value_label(f"{instance_id}_runtime_coordinates")
        self._add_metric(matrix, 0, 0, "局号", self.batch_id)
        self._add_metric(matrix, 0, 1, "倒计时", self.countdown)
        self._add_metric(matrix, 1, 0, "识别余额", self.balance)
        self._add_metric(matrix, 1, 1, "房号/限额", self.game_no)
        self._add_metric(matrix, 2, 0, "状态机", self.runtime_phase)
        self._add_metric(matrix, 2, 1, "坐标", self.runtime_coordinates)
        root.addLayout(matrix)

    def _value_label(self, object_name: str) -> QLabel:
        label = QLabel("-")
        label.setObjectName(object_name)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        return label

    def _add_metric(self, layout: QGridLayout, row: int, col: int, title: str, value: QLabel) -> None:
        box = QFrame()
        box.setObjectName("metricBox")
        box_layout = QVBoxLayout(box)
        box_layout.setContentsMargins(8, 6, 8, 6)
        name = QLabel(title)
        name.setObjectName("metricName")
        box_layout.addWidget(name)
        box_layout.addWidget(value)
        layout.addWidget(box, row, col)

    def _set_batch_id_text(self, value: object, fallback: str = "") -> None:
        raw = str(value or "").strip()
        raw = "" if raw == "-" else raw
        display = _stable_ui_batch_id(raw)
        self.batch_id.setText(display or fallback)
        if raw and display and display != raw:
            self.batch_id.setToolTip(f"完整原始局号：{raw}")
        else:
            self.batch_id.setToolTip("")

    def update_telemetry(self, telemetry: InstanceTelemetry) -> None:
        if self._has_recent_temporal_state():
            return
        state = "ready"
        if telemetry.status == "blocked":
            state = "blocked"
        elif telemetry.status == "error":
            state = "error"
        self.dot.set_state(state)
        self.title_label.setText(f"{telemetry.role} {telemetry.instance_id}")
        status_text = {"ready": "就绪", "blocked": "阻断", "error": "异常"}.get(
            telemetry.status,
            telemetry.status,
        )
        self.status_label.setText(status_text)
        self._set_batch_id_text(telemetry.batch_id)
        self.countdown.setText(f"{telemetry.countdown_seconds}秒")
        self.balance.setText(f"{telemetry.balance:.2f}")
        self.proxy.setText(f"{telemetry.proxy_state} · {telemetry.proxy_latency_ms}毫秒")
        self.fingerprint.setText(telemetry.fingerprint_state)

    def update_temporal_state(self, snapshot: TemporalStateSnapshot) -> None:
        self._last_temporal_update_ms = now_ms()
        self._set_batch_id_text(snapshot.batch_id, "等待 WS 状态")
        self.countdown.setText("未识别" if snapshot.exact_countdown < 0 else f"{snapshot.exact_countdown} 秒")
        self.balance.setText(snapshot.ocr_balance or "等待余额")
        self._update_runtime_details(snapshot)
        self.proxy.setText("WS 在线" if snapshot.ws_connected else "WS 断开")
        self.fingerprint.setText(f"{snapshot.source} · frame {snapshot.frame_id}")
        ws_age = int(snapshot.safe_summary.get("last_ws_age_ms", -1))
        self._set_ws_heartbeat(ws_age)
        if not snapshot.page_alive or not snapshot.ws_connected or ws_age > 300:
            self.dot.set_state("blocked")
            self.status_label.setText("状态失联阻断")
        else:
            self.dot.set_state("ready")
            self.status_label.setText("实时状态有效")
        self.refresh_shadow_display()

    def update_runtime_v2_shadow(self, payload: dict[str, Any]) -> None:
        accepted_state = payload.get("accepted_state") if isinstance(payload.get("accepted_state"), dict) else {}
        stable_state = payload.get("stable_state") if isinstance(payload.get("stable_state"), dict) else {}
        display_state = stable_state or accepted_state
        accepted_balance = payload.get("accepted_balance") if isinstance(payload.get("accepted_balance"), dict) else {}
        if not display_state:
            return
        current_ms = now_ms()
        batch_id = str(display_state.get("batch_id") or "").strip()
        phase_key = str(display_state.get("phase_key") or "").strip()
        raw_countdown = display_state.get("countdown")
        countdown = self._safe_int(raw_countdown)
        balance_text = str(
            accepted_balance.get("balance_text")
            or display_state.get("balance_text")
            or ""
        ).strip()
        stable_meta = display_state.get("evidence", {}).get("stable_shadow", {}) if isinstance(display_state.get("evidence"), dict) else {}
        self._shadow_display = {
            "last_update_ms": current_ms,
            "source": display_state.get("source") or "runtime_v2_shadow",
            "batch_id": batch_id,
            "room_label": display_state.get("room_label"),
            "limit_label": display_state.get("limit_label"),
            "balance_text": balance_text,
            "phase_key": phase_key,
            "phase_label": display_state.get("phase_label"),
            "betting_open": display_state.get("betting_open"),
            "stable_ready": InstanceCard._shadow_stable_ready(stable_meta),
            "stable_held": bool(stable_meta.get("held")) if isinstance(stable_meta, dict) else False,
        }
        self._update_shadow_countdown_anchor(batch_id, phase_key, countdown, current_ms)
        self.refresh_shadow_display(current_ms=current_ms)

    def refresh_shadow_display(self, current_ms: int | None = None) -> bool:
        display = self.__dict__.get("_shadow_display", {})
        if not isinstance(display, dict) or not display:
            return False
        current_ms = now_ms() if current_ms is None else int(current_ms)
        if current_ms - int(display.get("last_update_ms") or 0) > self.SHADOW_DISPLAY_TTL_MS:
            return False

        batch_id = str(display.get("batch_id") or "").strip()
        if batch_id:
            self._set_batch_id_text(batch_id, "等待影子局号")

        countdown = self._shadow_countdown_value(current_ms)
        if countdown is not None:
            self.countdown.setText(f"{countdown} 秒")

        balance_text = str(display.get("balance_text") or "").strip()
        if balance_text:
            self.balance.setText(balance_text)

        room_label = self._valid_room_label(display.get("room_label"))
        limit_label = self._valid_limit_label(display.get("limit_label"))
        room_limit = self._format_room_limit(room_label, limit_label)
        if room_limit:
            self.game_no.setText(room_limit)

        phase_key = str(display.get("phase_key") or "").strip()
        phase_label = self._runtime_phase_label(phase_key, str(display.get("phase_label") or "").strip())
        betting_open_value = display.get("betting_open")
        betting_open = bool(betting_open_value) if isinstance(betting_open_value, bool) else phase_key == "betting_open"
        open_flag = "可下注" if betting_open else "不可下注"
        self.runtime_phase.setText(f"{open_flag} · {phase_label}")
        self.runtime_phase.setToolTip(f"来源：{display.get('source') or 'runtime_v2_shadow'}")
        self.status_label.setText("影子状态有效")
        if display.get("stable_ready"):
            self.status_label.setText("影子状态有效")
        else:
            self.status_label.setText("影子状态恢复中")
        self.dot.set_state("ready")
        return True

    def _update_shadow_countdown_anchor(
        self,
        batch_id: str,
        phase_key: str,
        countdown: int | None,
        current_ms: int,
    ) -> None:
        if countdown is None or countdown < 0:
            return
        batch_key = _stable_ui_batch_id(batch_id) or str(batch_id or "").strip()
        current_value = self._shadow_countdown_value(current_ms)
        batch_changed = bool(batch_key and batch_key != getattr(self, "_shadow_countdown_batch_key", ""))
        phase_changed = bool(phase_key and phase_key != getattr(self, "_shadow_countdown_phase_key", ""))
        should_reset = (
            current_value is None
            or batch_changed
            or countdown < current_value
            or countdown > current_value + 2
            or (phase_changed and countdown > current_value)
        )
        if should_reset:
            self._shadow_countdown_anchor = int(countdown)
            self._shadow_countdown_anchor_ms = int(current_ms)
            self._shadow_countdown_batch_key = batch_key
        if phase_key:
            self._shadow_countdown_phase_key = phase_key

    def _shadow_countdown_value(self, current_ms: int | None = None) -> int | None:
        anchor = getattr(self, "_shadow_countdown_anchor", None)
        if anchor is None:
            return None
        current_ms = now_ms() if current_ms is None else int(current_ms)
        anchor_ms = int(getattr(self, "_shadow_countdown_anchor_ms", 0) or current_ms)
        elapsed_seconds = max(0, (current_ms - anchor_ms) // 1000)
        return max(0, int(anchor) - int(elapsed_seconds))

    @staticmethod
    def _shadow_stable_ready(stable_meta: object) -> bool:
        if not isinstance(stable_meta, dict) or stable_meta.get("held"):
            return False
        try:
            frame_count = int(stable_meta.get("frame_count") or 0)
            ready_frames = int(stable_meta.get("ready_frames") or 2)
        except (TypeError, ValueError):
            return False
        return frame_count >= max(1, ready_frames)

    def _has_recent_temporal_state(self) -> bool:
        return self._last_temporal_update_ms > 0

    def _update_runtime_details(self, snapshot: TemporalStateSnapshot) -> None:
        runtime = snapshot.safe_summary.get("runtime_state")
        has_runtime_summary = any(
            str(key).startswith("ws_runtime_")
            or str(key).startswith("frontend_runtime_")
            or str(key).startswith("runtime_")
            or str(key).startswith("label_")
            or str(key).startswith("frontend_")
            for key in snapshot.safe_summary
        )
        if not isinstance(runtime, dict) and not has_runtime_summary:
            self.game_no.setText("等待游戏内状态")
            self.runtime_phase.setText("等待")
            self.runtime_coordinates.setText("等待牌桌坐标")
            self.runtime_coordinates.setToolTip("")
            return
        if not isinstance(runtime, dict):
            runtime = {}

        room_label = self._valid_room_label(
            snapshot.safe_summary.get("locked_room_label"),
            snapshot.safe_summary.get("room_label"),
            snapshot.safe_summary.get("frontend_room_label"),
            snapshot.safe_summary.get("canvas_room_label"),
            snapshot.safe_summary.get("runtime_room_label"),
            snapshot.safe_summary.get("label_room_label"),
        )
        if not room_label:
            room_label = self._room_label_from_room_id(
                snapshot.safe_summary.get("locked_room_id")
                or snapshot.safe_summary.get("room_id")
                or snapshot.safe_summary.get("frontend_room_id")
                or snapshot.safe_summary.get("runtime_room_id")
            )
        limit_label = self._valid_limit_label(
            snapshot.safe_summary.get("frontend_limit_label"),
            snapshot.safe_summary.get("canvas_limit_label"),
            snapshot.safe_summary.get("runtime_limit_label"),
            snapshot.safe_summary.get("label_limit_label"),
            snapshot.safe_summary.get("limit_label"),
        )
        room_limit = self._format_room_limit(room_label, limit_label)
        self.game_no.setText(room_limit or "未识别")

        action = self._first_not_none(
            snapshot.safe_summary.get("frontend_runtime_action"),
            snapshot.safe_summary.get("runtime_action"),
            snapshot.safe_summary.get("ws_runtime_action"),
        )
        current_load_type = self._first_not_none(
            snapshot.safe_summary.get("frontend_runtime_current_load_type"),
            snapshot.safe_summary.get("runtime_current_load_type"),
            snapshot.safe_summary.get("ws_runtime_current_load_type"),
        )
        is_can_betting = self._first_not_none(
            snapshot.safe_summary.get("frontend_runtime_is_can_betting"),
            snapshot.safe_summary.get("runtime_is_can_betting"),
            snapshot.safe_summary.get("ws_runtime_is_can_betting"),
        )
        phase_text = str(
            snapshot.safe_summary.get("canvas_phase_text")
            or snapshot.safe_summary.get("label_phase_text")
            or snapshot.safe_summary.get("frontend_phase_text")
            or snapshot.safe_summary.get("phase_text")
            or ""
        ).strip()
        phase_key = self._phase_key_from_text(phase_text) or self._phase_key_from_values(
            action, is_can_betting, current_load_type, str(snapshot.safe_summary.get("phase_text", "unknown") or "unknown")
        )
        phase_label = self._runtime_phase_label(phase_key, phase_text)
        betting_open = phase_text != ""
        if phase_key == "betting_open":
            betting_open = True
        elif phase_key in {"dealing", "opening", "settling", "pre_bet", "betting_closed"}:
            betting_open = False
        if is_can_betting is not None and not phase_text and action is None:
            betting_open = bool(is_can_betting)
        open_flag = "可下注" if betting_open else "不可下注"
        self.runtime_phase.setText(f"{open_flag} · {phase_label}")
        self.runtime_phase.setToolTip("")

        coordinates = runtime.get("coordinates") if isinstance(runtime.get("coordinates"), dict) else {}
        if not coordinates and isinstance(snapshot.safe_summary.get("runtime_coordinates"), dict):
            coordinates = snapshot.safe_summary["runtime_coordinates"]
        chips = coordinates.get("chips") if isinstance(coordinates.get("chips"), dict) else {}
        bet_regions = coordinates.get("bet_regions") if isinstance(coordinates.get("bet_regions"), dict) else {}
        calibration = coordinates.get("calibration") if isinstance(coordinates.get("calibration"), dict) else {}
        if chips or bet_regions:
            viewport = runtime.get("viewport") if isinstance(runtime.get("viewport"), dict) else {}
            if not viewport and isinstance(snapshot.safe_summary.get("runtime_viewport"), dict):
                viewport = snapshot.safe_summary["runtime_viewport"]
            layout_confidence = runtime.get("layout_confidence")
            if layout_confidence is None:
                layout_confidence = snapshot.safe_summary.get("runtime_layout_confidence")
            self.runtime_coordinates.setText(f"筹码 {len(chips)} / 下注区 {len(bet_regions)}")
            tooltip = json.dumps(
                {
                    "chips": chips,
                    "bet_regions": bet_regions,
                    "viewport": viewport,
                    "layout_confidence": layout_confidence,
                    "calibration": calibration,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            self.runtime_coordinates.setToolTip(tooltip[:4000])
        else:
            self.runtime_coordinates.setText("等待牌桌坐标")
            self.runtime_coordinates.setToolTip("")

    @staticmethod
    def _runtime_phase_label(phase_key: str, fallback: str) -> str:
        labels = {
            "pre_bet": "准备中",
            "betting_open": "下注中",
            "betting_closed": "下注关闭",
            "dealing": "发牌中",
            "opening": "开牌中",
            "settling": "派彩中",
            "unknown": "未知状态",
        }
        return labels.get(phase_key, fallback or phase_key)

    @staticmethod
    def _first_not_none(*values: object) -> object:
        for value in values:
            if value is not None:
                return value
        return None

    @staticmethod
    def _safe_int(value: object) -> int | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        text = str(value or "").strip()
        if re.fullmatch(r"-?\d+", text):
            return int(text)
        return None

    @staticmethod
    def _phase_key_from_values(action: object, is_can_betting: object, current_load_type: object, fallback: str) -> str:
        try:
            action_value = int(action) if action is not None else None
        except Exception:
            action_value = None
        if action_value == 3:
            return "betting_open"
        if action_value in {2, 9}:
            return "pre_bet"
        if action_value in {10}:
            return "dealing"
        if action_value in {11, 12}:
            return "opening"
        if action_value in {5, 13}:
            return "settling"
        if is_can_betting is not None:
            return "betting_open" if bool(is_can_betting) else "betting_closed"
        if current_load_type is not None:
            return f"load_type_{current_load_type}"
        return fallback

    @staticmethod
    def _format_room_limit(room_label: str, limit_label: str) -> str:
        parts: list[str] = []
        if room_label:
            parts.append(room_label)
        if limit_label:
            parts.append(f"限额 {limit_label}")
        return " ".join(parts)

    @staticmethod
    def _valid_room_label(*values: object) -> str:
        for value in values:
            text = str(value or "").strip().upper()
            if re.fullmatch(r"T\d{3,}", text):
                return text
        return ""

    @staticmethod
    def _room_label_from_room_id(value: object) -> str:
        mapping = {"9101": "T001"}
        text = str(value or "").strip()
        if text in mapping:
            return mapping[text]
        if text == "182020001":
            return "T001"
        if re.fullmatch(r"\d{6,12}", text):
            return f"T{int(text[-3:]):03d}"
        return ""

    @staticmethod
    def _valid_limit_label(*values: object) -> str:
        for value in values:
            text = str(value or "").strip().replace(" ", "")
            if not text or re.search(r"(?<![\d-])\d{2,}-\d{6,}-\d{6,}-\d+(?!\d)", text):
                continue
            if re.fullmatch(r"\d{1,5}(?:\.\d+)?[-~]\d{1,5}(?:\.\d+)?", text):
                return text
        return ""

    @staticmethod
    def _limit_label_from_cents(low: object, high: object) -> str:
        try:
            low_value = int(low) if low is not None else 0
            high_value = int(high) if high is not None else 0
        except Exception:
            return ""
        if low_value <= 0 or high_value < low_value:
            return ""
        if low_value >= 100 and high_value >= 100:
            low_display = low_value // 100 if low_value % 100 == 0 else low_value / 100
            high_display = high_value // 100 if high_value % 100 == 0 else high_value / 100
            return f"{low_display:g}-{high_display:g}"
        return f"{low_value}-{high_value}"

    @staticmethod
    def _phase_key_from_text(text: str) -> str:
        if not text:
            return ""
        if "下注" in text and not any(marker in text for marker in ("不可", "关闭", "封盘", "停止")):
            return "betting_open"
        if "发牌" in text:
            return "dealing"
        if any(marker in text for marker in ("开牌", "亮牌")):
            return "opening"
        if any(marker in text for marker in ("准备", "等待", "休息")):
            return "pre_bet"
        if any(marker in text for marker in ("结算", "派彩")):
            return "settling"
        if any(marker in text for marker in ("关闭", "封盘", "停止")):
            return "betting_closed"
        return ""

    def update_cluster_health(self, payload: dict[str, Any]) -> None:
        if "last_ws_age_ms" in payload:
            self._set_ws_heartbeat(int(payload.get("last_ws_age_ms", -1)))
        if payload.get("websocket") == "closed":
            self._set_ws_heartbeat(999999)
            self.dot.set_state("blocked")
            self.status_label.setText("WS 已断开")
        elif payload.get("websocket") == "open":
            self._set_ws_heartbeat(0)

    def _set_ws_heartbeat(self, age_ms: int) -> None:
        if age_ms < 0:
            text = "WS 未连接"
            color = "#f87171"
            background = "#3f1d1d"
        elif age_ms <= 300:
            text = f"WS 心跳 {age_ms}ms"
            color = "#34d399"
            background = "#123627"
        else:
            text = "状态失联阻断"
            color = "#f87171"
            background = "#3f1d1d"
        self.ws_heartbeat.setText(text)
        self.ws_heartbeat.setStyleSheet(
            f"color: {color}; background-color: {background}; border: 1px solid {color}; "
            "border-radius: 5px; padding: 4px 8px; font-weight: bold;"
        )

    def update_browser_snapshot(self, snapshot: BrowserSnapshot) -> None:
        if self._has_recent_temporal_state():
            return
        status_text = snapshot.status or "运行中"
        self.status_label.setText(status_text)
        if "异常" in status_text or "失败" in status_text:
            self.dot.set_state("error")
        elif "未启动" in status_text or "阻断" in status_text:
            self.dot.set_state("blocked")
        else:
            self.dot.set_state("ready")
        if not snapshot.game_detected:
            self.batch_id.setText("等待进入游戏页")
            self.batch_id.setToolTip("")
            self.countdown.setText("等待进入游戏页")
            self.balance.setText("等待进入游戏页")
        else:
            self._set_batch_id_text(snapshot.batch_id, "未识别")
            countdown_text = "未识别" if snapshot.countdown_seconds < 0 else f"{snapshot.countdown_seconds}秒"
            self.countdown.setText(countdown_text)
            self.balance.setText(snapshot.balance_text or "未识别")
        self.proxy.setText(snapshot.proxy_state or "未检测")
        self.fingerprint.setText(snapshot.fingerprint_state or "未检测")


class DashboardProbeWorker(QThread):
    telemetry_ready = pyqtSignal(object)
    manifest_ready = pyqtSignal(object, str)
    hedge_delta_ready = pyqtSignal(float)
    log_ready = pyqtSignal(str)

    def __init__(self, page_registry: dict[str, Any] | None = None) -> None:
        super().__init__()
        self.page_registry = page_registry or {}
        self._stop_requested = False
        self._batch_seed = random.randint(40000, 90000)
        self._config_lock = RLock()
        self._config = DashboardRuntimeConfig(proxy_snapshot={}, account_snapshot={})

    def stop(self) -> None:
        self._stop_requested = True

    def set_base_amount(self, amount: int) -> None:
        with self._config_lock:
            self._config.base_amount = int(amount)
        preview = self.preview_decomposition(amount)
        self.log_ready.emit(f"[算法预览] 目标总额={amount} 拆分筹码={preview}")

    def set_hedge_model(self, model: str) -> None:
        with self._config_lock:
            self._config.hedge_model = model
        self.log_ready.emit(f"[策略配置] 对冲方向模型已切换：{model}")

    def update_proxy_snapshot(self, snapshot: dict[str, dict[str, str]]) -> None:
        with self._config_lock:
            self._config.proxy_snapshot = snapshot
        self.log_ready.emit(f"[代理配置] 已更新 4 路代理快照，敏感字段已隐藏")

    def update_account_snapshot(self, snapshot: dict[str, dict[str, str]]) -> None:
        with self._config_lock:
            self._config.account_snapshot = snapshot
        self.log_ready.emit("[账号配置] 已更新测试账号矩阵，Token 与 Cookie 不写入日志")

    def regenerate_fingerprint(self) -> None:
        with self._config_lock:
            self._config.fingerprint_version += 1
            version = self._config.fingerprint_version
        self.log_ready.emit(f"[指纹配置] 已刷新独立浏览器指纹环境，版本={version}")

    def reset_route_contexts(self) -> None:
        with self._config_lock:
            self._batch_seed += random.randint(100, 300)
        self.log_ready.emit("[账户路由] 已清除实例缓存并请求重新初始化 Playwright 沙箱")

    def preview_decomposition(self, amount: int) -> list[int]:
        return decompose_value(int(amount), DENOMINATIONS, max_steps=4)

    def _config_copy(self) -> DashboardRuntimeConfig:
        with self._config_lock:
            return DashboardRuntimeConfig(
                base_amount=self._config.base_amount,
                hedge_model=self._config.hedge_model,
                fingerprint_version=self._config.fingerprint_version,
                proxy_snapshot=dict(self._config.proxy_snapshot or {}),
                account_snapshot=dict(self._config.account_snapshot or {}),
            )

    def run(self) -> None:
        try:
            asyncio.run(self._probe_loop())
        except BaseException as exc:
            self.log_ready.emit(
                "[UI_THREAD_EXCEPTION] DashboardProbeWorker stopped: "
                + "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).strip()
            )
            raise

    async def _probe_loop(self) -> None:
        self.log_ready.emit("[真实监控] 已关闭模拟状态数据；状态面板只接受 Playwright 真实页面快照")
        while not self._stop_requested:
            await asyncio.sleep(2.0)

    def _make_demo_telemetry(self, batch_id: str, countdown: int) -> dict[str, InstanceTelemetry]:
        balances = {"a1": 361.83, "a2": 288.20, "a3": 412.05, "a4": 337.90}
        latencies = {"a1": 42, "a2": 58, "a3": 73, "a4": 49}
        roles = {"a1": "主号", "a2": "副号", "a3": "副号", "a4": "副号"}
        return {
            instance_id: InstanceTelemetry(
                instance_id=instance_id,
                role=roles[instance_id],
                status="ready",
                batch_id=batch_id,
                countdown_seconds=countdown - (1 if instance_id == "a2" else 0),
                balance=balances[instance_id] + random.uniform(-0.02, 0.02),
                proxy_latency_ms=latencies[instance_id] + random.randint(-3, 4),
                proxy_state="正常",
                fingerprint_state="已隔离",
            )
            for instance_id in INSTANCE_IDS
        }

    def _make_manifest(
        self,
        batch_id: str,
        telemetry: dict[str, InstanceTelemetry],
        config: DashboardRuntimeConfig,
    ) -> ShadowTransactionManifest:
        main_amount = int(config.base_amount)
        sub_allocations = self._split_sub_amount(main_amount)
        main_side, sub_side = self._resolve_hedge_sides(config.hedge_model)
        plan = {
            "a1": (main_side, main_amount),
            "a2": (sub_side, sub_allocations[0]),
            "a3": (sub_side, sub_allocations[1]),
            "a4": (sub_side, sub_allocations[2]),
        }
        instructions: list[ClickInstruction] = []
        sequence_index = 0
        for instance_id, (side, amount) in plan.items():
            chips = decompose_value(amount, DENOMINATIONS, max_steps=4)
            jitter_base = random.randint(120, 740)
            for chip in chips:
                sequence_index += 1
                chip_x, chip_y = self._chip_coordinate(chip)
                instructions.append(
                    ClickInstruction(
                        instance_id=instance_id,
                        target_x=chip_x,
                        target_y=chip_y,
                        delay_ms=jitter_base + sequence_index * 37,
                        batch_id=batch_id,
                        expected_amount=amount,
                        action="select_chip",
                        side=side,
                        sequence_index=sequence_index,
                        chip_value=chip,
                    )
                )
                sequence_index += 1
                side_x, side_y = self._side_coordinate(side)
                instructions.append(
                    ClickInstruction(
                        instance_id=instance_id,
                        target_x=side_x,
                        target_y=side_y,
                        delay_ms=jitter_base + sequence_index * 37,
                        batch_id=batch_id,
                        expected_amount=amount,
                        action="click_side",
                        side=side,
                        sequence_index=sequence_index,
                        chip_value=chip,
                    )
                )

        precheck_snapshot = {
            instance_id: {
                "batch_id": item.batch_id,
                "countdown_seconds": item.countdown_seconds,
                "proxy_state": item.proxy_state,
                "proxy_latency_ms": item.proxy_latency_ms,
                "fingerprint_state": item.fingerprint_state,
                "ocr_balance": round(item.balance, 2),
            }
            for instance_id, item in telemetry.items()
        }
        expectation = HedgeExpectation(
            main_instance_id="a1",
            main_side=main_side,
            main_amount=main_amount,
            opposite_side=sub_side,
            sub_total_amount=sum(sub_allocations),
        )
        return ShadowTransactionManifest.build(
            manifest_id=f"影子-{uuid4()}",
            batch_id=batch_id,
            instructions=instructions,
            precheck_snapshot=precheck_snapshot,
            hedge_expectation=expectation,
            metadata={
                "运行模式": "影子审计模式",
                "界面来源": "桌面主界面",
                "真实执行": "已阻断",
                "对冲方向模型": config.hedge_model,
                "基础目标总额": config.base_amount,
                "指纹版本": config.fingerprint_version,
                "代理配置实例数": len(config.proxy_snapshot or {}),
                "账号配置实例数": len(config.account_snapshot or {}),
            },
        )

    @staticmethod
    def _split_sub_amount(amount: int) -> tuple[int, int, int]:
        policy = HedgePlanPolicy(
            denominations=DENOMINATIONS,
            main_amount_min=int(amount),
            main_amount_max=int(amount),
            sub_account_count=3,
            max_chip_steps_per_account=4,
            max_total_chip_steps=16,
        )
        plan = RandomHedgePlanGenerator(policy).generate(
            main_account_id="a1",
            sub_account_ids=("a2", "a3", "a4"),
            main_side="main",
            opposite_side="sub",
            main_amount=int(amount),
        )
        return tuple(leg.amount for leg in plan.legs if leg.role == "sub")  # type: ignore[return-value]

    @staticmethod
    def _resolve_hedge_sides(model: str) -> tuple[str, str]:
        if "主庄" in model:
            return "庄", "闲"
        if "多向" in model:
            main_side = random.choice(("闲", "庄"))
            return main_side, "庄" if main_side == "闲" else "闲"
        return "闲", "庄"

    @staticmethod
    def _chip_coordinate(chip: int) -> tuple[float, float]:
        return {
            4: (166.0, 704.0),
            10: (230.0, 704.0),
            20: (294.0, 704.0),
            50: (358.0, 704.0),
            100: (422.0, 704.0),
        }[chip]

    @staticmethod
    def _side_coordinate(side: str) -> tuple[float, float]:
        return {"闲": (166.0, 586.0), "庄": (394.0, 586.0), "和": (280.0, 624.0)}[side]


class BrowserRuntimeWorker(QThread):
    snapshot_ready = pyqtSignal(object)
    log_ready = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self._commands: Queue[tuple[str, object | None]] = Queue()
        self._stop_requested = False

    def launch_browsers(self, configs: list[BrowserInstanceConfig]) -> None:
        self._commands.put(("launch", configs))

    def fill_login_forms(self) -> None:
        self._commands.put(("login", None))

    def fill_login_forms_with_configs(self, configs: list[BrowserInstanceConfig]) -> None:
        self._commands.put(("login_with_configs", configs))

    def open_target_pages(self, configs: list[BrowserInstanceConfig] | None = None) -> None:
        self._commands.put(("target", configs))

    def capture_game_launch_context(self, configs: list[BrowserInstanceConfig] | None = None) -> None:
        self._commands.put(("capture_launch_context", configs))

    def handoff_to_headless(self, configs: list[BrowserInstanceConfig] | None = None) -> None:
        self._commands.put(("handoff_headless", configs))

    def enter_room(self, room_index: int, configs: list[BrowserInstanceConfig] | None = None) -> None:
        self._commands.put(("enter_room", {"room_index": int(room_index), "configs": configs}))

    def refresh_headless_status(self, configs: list[BrowserInstanceConfig] | None = None) -> None:
        self._commands.put(("refresh_headless", configs))

    def release_headless(self, configs: list[BrowserInstanceConfig] | None = None) -> None:
        self._commands.put(("release_headless", configs))

    def stop_browsers(self) -> None:
        self._commands.put(("close", None))

    def stop(self) -> None:
        self._stop_requested = True
        self._commands.put(("shutdown", None))

    def run(self) -> None:
        try:
            asyncio.run(self._run_loop())
        except BaseException as exc:
            self.log_ready.emit(
                "[UI_THREAD_EXCEPTION] BrowserRuntimeWorker stopped: "
                + "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).strip()
            )
            raise

    async def _run_loop(self) -> None:
        manager = BrowserSessionManager()
        try:
            last_snapshot = 0.0
            while not self._stop_requested:
                await self._drain_commands(manager)
                now = time.monotonic()
                if now - last_snapshot >= 1.5:
                    await self._emit_snapshots(manager)
                    last_snapshot = now
                await asyncio.sleep(0.15)
        finally:
            await manager.stop()

    async def _drain_commands(self, manager: BrowserSessionManager) -> None:
        while True:
            try:
                command, payload = self._commands.get_nowait()
            except Empty:
                return
            try:
                if command == "launch":
                    configs = payload if isinstance(payload, list) else []
                    if not configs:
                        self.log_ready.emit("[浏览器] 缺少实例配置，未启动")
                        continue
                    self.log_ready.emit("[浏览器] 正在启动 4 路独立浏览器实例")
                    snapshots = await manager.launch_instances(configs)
                    for snapshot in snapshots:
                        self.snapshot_ready.emit(snapshot)
                    self.log_ready.emit("[浏览器] 4 路实例启动完成")
                elif command == "login":
                    if not manager.pages():
                        self.log_ready.emit("[登录测试] 请先启动浏览器，或使用左侧配置发起一键登录测试")
                        continue
                    self.log_ready.emit("[登录测试] 正在填写账号密码并提交登录表单")
                    snapshots = await manager.fill_login_forms()
                    for snapshot in snapshots:
                        self.snapshot_ready.emit(snapshot)
                    self.log_ready.emit("[登录测试] 已提交登录表单，若出现验证码请人工完成")
                elif command == "login_with_configs":
                    configs = payload if isinstance(payload, list) else []
                    if not configs:
                        self.log_ready.emit("[登录测试] 缺少实例配置，未执行")
                        continue
                    configured_ids = [item.instance_id for item in configs if isinstance(item, BrowserInstanceConfig)]
                    active_pages = manager.pages()
                    if any(instance_id not in active_pages for instance_id in configured_ids):
                        self.log_ready.emit("[登录测试] 尚未启动浏览器，正在按当前配置启动实例")
                        snapshots = await manager.launch_instances(configs)
                        for snapshot in snapshots:
                            self.snapshot_ready.emit(snapshot)
                        self.log_ready.emit("[登录测试] 浏览器启动完成，开始提交登录表单")
                    snapshots = await manager.fill_login_forms(configured_ids)
                    for snapshot in snapshots:
                        self.snapshot_ready.emit(snapshot)
                    self.log_ready.emit("[登录测试] 已提交登录表单，若出现验证码请人工完成")
                elif command == "target":
                    self.log_ready.emit("[目标页面] 正在进入目标游戏页面")
                    snapshots = await manager.open_targets()
                    for snapshot in snapshots:
                        self.snapshot_ready.emit(snapshot)
                    self.log_ready.emit("[目标页面] 目标页导航完成")
                elif command == "close":
                    await manager.close_all_instances()
                    self.log_ready.emit("[浏览器] 已停止全部实例")
                elif command == "shutdown":
                    self._stop_requested = True
                    return
            except Exception as exc:
                self.log_ready.emit(f"[浏览器异常] {command} 失败：{exc}")

    async def _emit_snapshots(self, manager: BrowserSessionManager) -> None:
        try:
            snapshots = await manager.snapshot_all()
        except Exception as exc:
            self.log_ready.emit(f"[页面截图] 更新失败：{exc}")
            return
        for snapshot in snapshots:
            self.snapshot_ready.emit(snapshot)


class ClusterRuntimeWorker(QThread):
    state_ready = pyqtSignal(object)
    health_ready = pyqtSignal(str, object)
    ws_raw_ready = pyqtSignal(object)
    audit_ready = pyqtSignal(str, object)
    runtime_v2_shadow_ready = pyqtSignal(str, object)
    log_ready = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self._commands: Queue[tuple[str, object | None]] = Queue()
        self._stop_requested = False
        self._controller: ClusterProcessController | None = None
        self._last_configs: list[BrowserInstanceConfig] = []
        self._handoff_queue: list[BrowserInstanceConfig] = []
        self._handoff_active: set[str] = set()
        self._enter_room_queue: list[tuple[BrowserInstanceConfig, int]] = []
        self._enter_room_active: set[str] = set()
        self._handoff_parallel_limit = 2
        self._enter_room_parallel_limit = 4
        self._runtime_v2_shadow_log_path: Path | None = None

    def launch_browsers(self, configs: list[BrowserInstanceConfig]) -> None:
        self._commands.put(("launch", configs))

    def restart_platform(self, configs: list[BrowserInstanceConfig]) -> None:
        self._commands.put(("restart_platform", configs))

    def fill_login_forms_with_configs(self, configs: list[BrowserInstanceConfig]) -> None:
        self._commands.put(("login", configs))

    def fill_current_login_forms_with_configs(self, configs: list[BrowserInstanceConfig]) -> None:
        self._commands.put(("fill_current_login", configs))

    def open_target_pages(self, configs: list[BrowserInstanceConfig] | None = None) -> None:
        self._commands.put(("target", configs))

    def capture_game_launch_context(self, configs: list[BrowserInstanceConfig] | None = None) -> None:
        self._commands.put(("capture_launch_context", configs))

    def handoff_to_headless(self, configs: list[BrowserInstanceConfig] | None = None) -> None:
        self._commands.put(("handoff_headless", configs))

    def enter_room(self, room_index: int, configs: list[BrowserInstanceConfig] | None = None) -> None:
        self._commands.put(("enter_room", {"room_index": int(room_index), "configs": configs}))

    def refresh_headless_status(self, configs: list[BrowserInstanceConfig] | None = None) -> None:
        self._commands.put(("refresh_headless", configs))

    def release_headless(self, configs: list[BrowserInstanceConfig] | None = None) -> None:
        self._commands.put(("release_headless", configs))

    def stop_browsers(self) -> None:
        self._commands.put(("close", None))

    def stop(self) -> None:
        self._stop_requested = True
        self._commands.put(("shutdown", None))

    def run(self) -> None:
        try:
            while not self._stop_requested:
                self._drain_commands()
                self._poll_cluster_events()
                time.sleep(0.03)
        except BaseException as exc:
            self.log_ready.emit(
                "[CLUSTER_THREAD_EXCEPTION] "
                + "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).strip()
            )
            raise
        finally:
            self._stop_controller()

    def _drain_commands(self) -> None:
        while True:
            try:
                command, payload = self._commands.get_nowait()
            except Empty:
                return
            if command == "shutdown":
                self._stop_requested = True
                return
            if command == "close":
                self._stop_controller()
                self.log_ready.emit("[集群状态源] 已停止 4 路独立状态采集进程")
                continue
            configs = payload if isinstance(payload, list) else self._last_configs
            if isinstance(payload, dict) and isinstance(payload.get("configs"), list):
                configs = payload["configs"]
            if isinstance(payload, list) and command == "target":
                self._last_configs = [item for item in payload if isinstance(item, BrowserInstanceConfig)]
                configs = self._last_configs
            if command == "fill_current_login":
                fill_configs = [item for item in configs if isinstance(item, BrowserInstanceConfig)]
                if fill_configs:
                    self._last_configs = fill_configs
                self._fill_current_login_forms(fill_configs or self._last_configs)
                continue
            if command == "restart_platform":
                valid_configs = [item for item in configs if isinstance(item, BrowserInstanceConfig)]
                if not valid_configs:
                    self.log_ready.emit("[单路重接] 缺少平台配置，未重启")
                    continue
                if self._controller is not None:
                    restart_ids = [item.instance_id for item in valid_configs]
                    self._controller.stop_instances(restart_ids)
                    self.log_ready.emit("[单路重接] 已停止指定平台旧会话：" + ", ".join(restart_ids))
                self._last_configs = valid_configs
                self._start_controller(valid_configs, mode="login")
                continue
            if command in {"capture_launch_context", "handoff_headless"}:
                if self._controller is None:
                    self.log_ready.emit("[大厅接管] 浏览器还没有启动，请先启动浏览器并人工到达百家乐大厅")
                    continue
                if isinstance(payload, list):
                    self._last_configs = [item for item in payload if isinstance(item, BrowserInstanceConfig)]
                    configs = self._last_configs
                worker_command = (
                    "capture_game_launch_context" if command == "capture_launch_context" else "handoff_to_headless"
                )
                if command == "handoff_headless":
                    valid_configs = [item for item in configs if isinstance(item, BrowserInstanceConfig)]
                    if valid_configs:
                        self._last_configs = valid_configs
                    queued = self._queue_handoff(valid_configs or self._last_configs)
                    self.log_ready.emit(
                        f"[大厅接管] 已排队 {queued} 路无头接管，最多 {self._handoff_parallel_limit} 路同时执行"
                    )
                    continue
                sent = 0
                for item in configs:
                    if not isinstance(item, BrowserInstanceConfig):
                        continue
                    if self._controller.send_command(item.instance_id, {"command": worker_command}):
                        sent += 1
                label = "捕获大厅启动包" if command == "capture_launch_context" else "无头接管大厅"
                self.log_ready.emit(f"[大厅接管] 已向 {sent} 路浏览器发送{label}命令")
                continue
            if command in {"refresh_headless", "release_headless"}:
                if self._controller is None:
                    self.log_ready.emit("[无头接管] 当前没有运行中的浏览器进程，无法管理无头会话")
                    continue
                if isinstance(payload, list):
                    self._last_configs = [item for item in payload if isinstance(item, BrowserInstanceConfig)]
                    configs = self._last_configs
                valid_configs = [item for item in configs if isinstance(item, BrowserInstanceConfig)]
                if valid_configs:
                    self._last_configs = valid_configs
                worker_command = "report_headless_status" if command == "refresh_headless" else "release_headless"
                sent = 0
                for item in valid_configs or self._last_configs:
                    if not isinstance(item, BrowserInstanceConfig):
                        continue
                    if self._controller.send_command(item.instance_id, {"command": worker_command}):
                        sent += 1
                label = "刷新无头状态" if command == "refresh_headless" else "释放无头接管"
                self.log_ready.emit(f"[无头接管] 已向 {sent} 路浏览器发送{label}命令")
                continue
            if command == "enter_room":
                if self._controller is None:
                    self.log_ready.emit("[进入房间] 浏览器还没有启动，请先完成无头接管大厅")
                    continue
                room_index = 0
                if isinstance(payload, dict):
                    try:
                        room_index = int(payload.get("room_index") or 0)
                    except (TypeError, ValueError):
                        room_index = 0
                if room_index not in {1, 2, 3, 4}:
                    self.log_ready.emit(f"[进入房间] 房间编号无效：{room_index}")
                    continue
                valid_configs = [item for item in configs if isinstance(item, BrowserInstanceConfig)]
                if valid_configs:
                    self._last_configs = valid_configs
                queued = self._queue_enter_room(valid_configs or self._last_configs, room_index)
                self.log_ready.emit(
                    f"[进入房间] 已排队 {queued} 路进入房间 {room_index}，最多 {self._enter_room_parallel_limit} 路同时执行"
                )
                continue
            if command in {"launch", "login"}:
                self._last_configs = [item for item in configs if isinstance(item, BrowserInstanceConfig)]
                self._start_controller(self._last_configs, mode=command)
            elif command == "target":
                if self._controller is not None:
                    sent = 0
                    for item in configs:
                        if not isinstance(item, BrowserInstanceConfig) or not item.target_url:
                            continue
                        if self._controller.send_command(
                            item.instance_id,
                            {"command": "navigate", "url": item.target_url},
                        ):
                            sent += 1
                    self.log_ready.emit(f"[目标页面] 已向 {sent} 路当前浏览器发送目标页导航命令")
                    continue
                    self.log_ready.emit("[集群状态源] 已保持当前登录浏览器，请在浏览器内人工进入目标游戏页；WS/页面状态采集会持续监听")
                    continue
                self._start_controller(self._last_configs, mode="target")

    def _start_controller(self, configs: list[BrowserInstanceConfig], *, mode: str) -> None:
        if not configs:
            self.log_ready.emit("[集群状态源] 缺少实例配置，未启动")
            return
        cluster_configs = [self._to_cluster_config(item, mode=mode) for item in configs]
        if self._controller is None:
            self._controller = ClusterProcessController(cluster_configs)
            self._controller.start()
            self.log_ready.emit(f"[集群状态源] 已启动 {len(cluster_configs)} 路独立进程，模式={mode}，WS 状态成为主状态源")
            return
        running_ids = self._controller.running_instance_ids()
        missing_configs = [item for item in cluster_configs if item.instance_id not in running_ids]
        if not missing_configs:
            self.log_ready.emit("[集群状态源] 所选平台已经在运行，未重复启动")
            return
        self._controller.start_configs(missing_configs)
        self.log_ready.emit(f"[集群状态源] 已追加启动 {len(missing_configs)} 路独立进程，模式={mode}，已运行平台保持不变")

    def _fill_current_login_forms(self, configs: list[BrowserInstanceConfig]) -> None:
        if self._controller is None:
            self.log_ready.emit("[登录流程] 浏览器还没有启动，请先点击“启动4路浏览器”，打开并切到登录页后再填写")
            return
        submitted = 0
        for item in configs:
            if not isinstance(item, BrowserInstanceConfig):
                continue
            if not item.account.username or not item.account.password:
                continue
            ok = self._controller.send_command(
                item.instance_id,
                {
                    "command": "fill_login",
                    "username": item.account.username,
                    "password": item.account.password,
                },
            )
            if ok:
                submitted += 1
        self.log_ready.emit(f"[登录流程] 已向 {submitted} 路当前浏览器发送填写登录表单命令")

    def _queue_handoff(self, configs: list[BrowserInstanceConfig]) -> int:
        queued_ids = {item.instance_id for item in self._handoff_queue}
        added = 0
        for item in configs:
            if not isinstance(item, BrowserInstanceConfig):
                continue
            if item.instance_id in self._handoff_active or item.instance_id in queued_ids:
                continue
            self._handoff_queue.append(item)
            queued_ids.add(item.instance_id)
            added += 1
        self._pump_handoff_queue()
        return added

    def _pump_handoff_queue(self) -> None:
        if self._controller is None:
            return
        while len(self._handoff_active) < self._handoff_parallel_limit and self._handoff_queue:
            item = self._handoff_queue.pop(0)
            if self._controller.send_command(item.instance_id, {"command": "handoff_to_headless"}):
                self._handoff_active.add(item.instance_id)
                self.log_ready.emit(f"[大厅接管] {item.instance_id}: 已开始无头接管")

    def _queue_enter_room(self, configs: list[BrowserInstanceConfig], room_index: int) -> int:
        queued_ids = {item.instance_id for item, _room_index in self._enter_room_queue}
        added = 0
        for item in configs:
            if not isinstance(item, BrowserInstanceConfig):
                continue
            if item.instance_id in self._enter_room_active or item.instance_id in queued_ids:
                continue
            self._enter_room_queue.append((item, room_index))
            queued_ids.add(item.instance_id)
            added += 1
        self._pump_enter_room_queue()
        return added

    def _pump_enter_room_queue(self) -> None:
        if self._controller is None:
            return
        while len(self._enter_room_active) < self._enter_room_parallel_limit and self._enter_room_queue:
            item, room_index = self._enter_room_queue.pop(0)
            if self._controller.send_command(
                item.instance_id,
                {"command": "enter_room", "room_index": room_index},
            ):
                self._enter_room_active.add(item.instance_id)
                self.log_ready.emit(f"[进入房间] {item.instance_id}: 已发送进入房间 {room_index} 命令")

    def _finish_handoff_if_active(self, instance_id: str) -> None:
        if instance_id in self._handoff_active:
            self._handoff_active.discard(instance_id)
            self._pump_handoff_queue()

    def _finish_enter_room_if_active(self, instance_id: str) -> None:
        if instance_id in self._enter_room_active:
            self._enter_room_active.discard(instance_id)
            self._pump_enter_room_queue()

    def _stop_controller(self) -> None:
        if self._controller is not None:
            self._controller.stop()
            self._controller = None
        self._handoff_queue.clear()
        self._handoff_active.clear()
        self._enter_room_queue.clear()
        self._enter_room_active.clear()

    def _poll_cluster_events(self) -> None:
        if self._controller is None:
            return
        for event in self._controller.poll_events(max_items=128):
            if event.event_type == "state":
                self.state_ready.emit(TemporalStateSnapshot.from_mapping(event.payload))
            elif event.event_type == "health":
                self.health_ready.emit(event.instance_id, event.payload)
                launch_state = str(event.payload.get("game_launch_context") or "")
                if launch_state in {"headless_ready", "headless_released"}:
                    self._finish_handoff_if_active(event.instance_id)
                room_entry = str(event.payload.get("room_entry") or "")
                if room_entry in {"game_ready", "click_confirmed", "timeout"}:
                    self._finish_enter_room_if_active(event.instance_id)
                if "login_auto_fill" in event.payload:
                    self.log_ready.emit(
                        f"[登录流程] {event.instance_id}: {event.payload.get('login_auto_fill')} "
                        f"stage={event.payload.get('stage', '')} submit={event.payload.get('clicked_submit', '')}"
                    )
            elif event.event_type == "ws_raw":
                self.ws_raw_ready.emit(event.payload)
            elif event.event_type == "audit":
                self.audit_ready.emit(event.instance_id, event.payload)
            elif event.event_type == "state_v2_shadow":
                data = event.payload if isinstance(event.payload, dict) else {}
                self._append_runtime_v2_shadow_event(event.instance_id, data)
                self.runtime_v2_shadow_ready.emit(event.instance_id, data)
                accepted_state = data.get("accepted_state") if isinstance(data.get("accepted_state"), dict) else {}
                stable_state = data.get("stable_state") if isinstance(data.get("stable_state"), dict) else {}
                display_state = stable_state or accepted_state
                accepted_balance = data.get("accepted_balance") if isinstance(data.get("accepted_balance"), dict) else {}
                session = data.get("session") if isinstance(data.get("session"), dict) else {}
                countdown = display_state.get("countdown")
                self.log_ready.emit(
                    "[runtime-v2-shadow] "
                    f"{event.instance_id}: "
                    f"room={display_state.get('room_label') or session.get('expected_room_label') or '-'} "
                    f"batch={display_state.get('batch_id') or '-'} "
                    f"cd={countdown if countdown is not None else '-'} "
                    f"phase={display_state.get('phase_key') or '-'} "
                    f"balance={accepted_balance.get('balance_text') or '-'} "
                    f"candidates={data.get('candidate_count', 0)} "
                    f"rejected={len(data.get('rejected') or [])}"
                )
            elif event.event_type == "error":
                self._finish_handoff_if_active(event.instance_id)
                self._finish_enter_room_if_active(event.instance_id)
                self.log_ready.emit(f"[集群状态源异常] {event.instance_id}: {event.payload}")
            elif event.event_type == "log":
                self.log_ready.emit(f"[集群状态源] {event.instance_id}: {event.payload.get('message', '')}")

    def _append_runtime_v2_shadow_event(self, instance_id: str, payload: dict[str, Any]) -> None:
        try:
            if self._runtime_v2_shadow_log_path is None:
                RUNTIME_V2_SHADOW_LOG_DIR.mkdir(parents=True, exist_ok=True)
                self._runtime_v2_shadow_log_path = (
                    RUNTIME_V2_SHADOW_LOG_DIR / f"ui_runtime_v2_shadow_{time.strftime('%Y%m%d_%H%M%S')}.jsonl"
                )
            record = {
                "timestamp_ms": now_ms(),
                "instance_id": str(instance_id),
                "payload": payload,
            }
            with self._runtime_v2_shadow_log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        except Exception:
            return

    @staticmethod
    def _to_cluster_config(config: BrowserInstanceConfig, *, mode: str) -> ClusterWorkerConfig:
        target_url = config.target_url if mode == "target" else ""
        profile_dir = PROJECT_ROOT / "bet_desktop" / "artifacts" / "profiles" / config.instance_id
        runtime_pipeline = os.environ.get("BET_DESKTOP_RUNTIME_PIPELINE", DEFAULT_RUNTIME_PIPELINE).strip() or DEFAULT_RUNTIME_PIPELINE
        try:
            runtime_shadow_interval_ms = int(os.environ.get("BET_DESKTOP_RUNTIME_SHADOW_INTERVAL_MS", "1000"))
        except ValueError:
            runtime_shadow_interval_ms = 1000
        return ClusterWorkerConfig(
            instance_id=config.instance_id,
            login_url=config.login_url,
            target_url=target_url,
            username=config.account.username,
            password=config.account.password,
            auto_fill_login=(mode == "login"),
            headless=config.headless,
            proxy={
                "host": config.proxy.host,
                "port": config.proxy.port,
                "username": config.proxy.username,
                "password": config.proxy.password,
            },
            user_data_dir=str(profile_dir),
            browser_channel="chrome",
            ws_parser_path="bet_desktop.parsers.ws_protocol_parser:HeuristicWSParser",
            runtime_pipeline=runtime_pipeline,
            runtime_shadow_interval_ms=runtime_shadow_interval_ms,
            debug_port=_debug_port_for_instance(config.instance_id),
        )


class _StaticSafeDetector:
    def __init__(self, batch_id: str, countdown_seconds: int = 9) -> None:
        self.batch_id = batch_id
        self.countdown_seconds = countdown_seconds

    async def __call__(self, _page: Any) -> PageStateSnapshot:
        return PageStateSnapshot(
            is_processing=True,
            countdown_seconds=self.countdown_seconds,
            batch_id=self.batch_id,
            safe_summary={"source": "ui_static_fail_closed_probe"},
        )


class _NoClickPage:
    async def evaluate(self, _expression: str, *args: Any) -> Any:
        return False

    async def screenshot(self, **_kwargs: Any) -> bytes:
        return ONE_PIXEL_PNG

    async def wait_for_timeout(self, timeout_ms: float) -> None:
        await asyncio.sleep(max(0.0, float(timeout_ms)) / 1000)


def manifest_to_chinese_json(manifest: ShadowTransactionManifest) -> str:
    action_names = {"select_chip": "选择筹码", "click_side": "点击区域"}
    payload = {
        "清单编号": manifest.manifest_id,
        "创建时间": manifest.created_at,
        "批次号": manifest.batch_id,
        "指令列表": [
            {
                "实例编号": item.instance_id,
                "目标横坐标": item.target_x,
                "目标纵坐标": item.target_y,
                "延迟毫秒": item.delay_ms,
                "批次号": item.batch_id,
                "预期金额": item.expected_amount,
                "动作": action_names.get(item.action, item.action),
                "方向": item.side,
                "序号": item.sequence_index,
                "筹码": item.chip_value,
            }
            for item in manifest.instructions
        ],
        "预检快照": {
            instance_id: {
                "批次号": snapshot.get("batch_id"),
                "倒计时秒数": snapshot.get("countdown_seconds"),
                "代理状态": snapshot.get("proxy_state"),
                "代理延迟毫秒": snapshot.get("proxy_latency_ms"),
                "指纹状态": snapshot.get("fingerprint_state"),
                "识别余额": snapshot.get("ocr_balance"),
            }
            for instance_id, snapshot in manifest.precheck_snapshot.items()
        },
        "对冲预期": {
            "主号实例": manifest.hedge_expectation.main_instance_id,
            "主号方向": manifest.hedge_expectation.main_side,
            "主号金额": manifest.hedge_expectation.main_amount,
            "副号反向": manifest.hedge_expectation.opposite_side,
            "副号合计金额": manifest.hedge_expectation.sub_total_amount,
            "预期校验码": manifest.hedge_expectation.checksum,
        },
        "清单校验码": manifest.manifest_checksum,
        "元数据": manifest.metadata,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False)


class SimulationFireWorker(QThread):
    blocked = pyqtSignal(object)
    failed = pyqtSignal(str)
    log_ready = pyqtSignal(str)

    def __init__(self, manifest: ShadowTransactionManifest) -> None:
        super().__init__()
        self.manifest = manifest

    def run(self) -> None:
        try:
            asyncio.run(self._run_fail_closed_step())
        except BaseException as exc:
            self.finished_with_error.emit(
                "[UI_THREAD_EXCEPTION] SimulationFireWorker stopped: "
                + "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).strip()
            )

    async def _run_fail_closed_step(self) -> None:
        instruction = self._pick_first_side_click(self.manifest)
        bridge = LiveExecutionBridge()
        driver = PlaywrightLiveDriver(
            {instruction.instance_id: _NoClickPage()},
            detector=_StaticSafeDetector(instruction.batch_id),
        )
        self.log_ready.emit(
            "[真实执行] 桥接器已加载显式授权；驱动仍会在物理点击前故障即关闭"
        )
        try:
            await self._engage_single_instruction(bridge, driver, instruction)
        except LiveDriverBlocked as exc:
            trace_id = str(exc.safe_summary.get("trace_id", "unknown"))
            self.blocked.emit(
                {
                    "trace_id": trace_id,
                    "instance_id": instruction.instance_id,
                    "target_x": instruction.target_x,
                    "target_y": instruction.target_y,
                    "batch_id": instruction.batch_id,
                    "expected_amount": instruction.expected_amount,
                    "action": instruction.action,
                    "side": instruction.side,
                }
            )
        except AutomationException as exc:
            self.failed.emit(f"{exc.code.value}: {exc.message}")
        except Exception as exc:  # pragma: no cover - UI safety boundary
            self.failed.emit(str(exc))

    async def _engage_single_instruction(
        self,
        bridge: LiveExecutionBridge,
        driver: PlaywrightLiveDriver,
        instruction: ClickInstruction,
    ) -> None:
        if not self.manifest.verify_checksum():
            raise RuntimeError("影子清单校验失败，不能进入故障即关闭仿真")
        if not getattr(driver, "live_execution", False) or getattr(driver, "sandbox_safe", True):
            raise RuntimeError("驱动不是显式真实执行故障即关闭驱动")
        if bridge.REQUIRED_TOKEN != "ACK_LIVE_EXECUTION_RISK":
            raise RuntimeError("真实桥接器授权令牌异常")

        # LiveExecutionBridge.engage_live_workflow aggregates AutomationException
        # into a result object. For this UI control we deliberately execute the
        # same driver pipeline directly so the QMessageBox can show the exact
        # LiveDriverBlocked trace ID and intercepted coordinates.
        await driver.execute_live_click_pipeline(instruction)

    @staticmethod
    def _pick_first_side_click(manifest: ShadowTransactionManifest) -> ClickInstruction:
        for instruction in manifest.instructions:
            if instruction.action == "click_side":
                return instruction
        return manifest.instructions[0]


class GlobalControllerPanel(QFrame):
    amount_changed = pyqtSignal(int)
    hedge_model_changed = pyqtSignal(str)
    proxy_snapshot_changed = pyqtSignal(object)
    account_snapshot_changed = pyqtSignal(object)
    regenerate_fingerprint_requested = pyqtSignal()
    reset_route_requested = pyqtSignal()
    launch_browser_requested = pyqtSignal(object)
    restart_platform_requested = pyqtSignal(object)
    login_requested = pyqtSignal(object)
    fill_current_login_requested = pyqtSignal(object)
    open_target_requested = pyqtSignal()
    handoff_headless_requested = pyqtSignal(object)
    enter_room_requested = pyqtSignal(int, object)
    refresh_headless_requested = pyqtSignal(object)
    release_headless_requested = pyqtSignal(object)
    stop_browser_requested = pyqtSignal()
    start_execution_requested = pyqtSignal()
    pause_execution_requested = pyqtSignal()
    stop_execution_requested = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("globalControllerPanel")
        self.setMinimumWidth(500)
        self.setMaximumWidth(540)
        self._platform_profile_path = PROJECT_ROOT / "bet_desktop" / "artifacts" / "platform_proxy_profiles.json"
        self._platform_profiles: dict[str, dict[str, Any]] = {}
        self._headless_status: dict[str, str] = {instance_id: "未接管" for instance_id in INSTANCE_IDS}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        title = QLabel("全局控制配置")
        title.setObjectName("controllerTitle")
        layout.addWidget(title)

        self._build_legacy_account_widgets()
        self.controller_tabs = QTabWidget()
        self.controller_tabs.setObjectName("controllerModeTabs")
        self.controller_tabs.addTab(self._build_proxy_section(), "四平台群控")
        self.controller_tabs.addTab(self._build_test_case_section(), "执行与算法")
        layout.addWidget(self.controller_tabs, 1)
        self._load_platform_profiles()
        self.update_headless_progress()

    def update_headless_progress(self, updates: dict[str, str] | None = None) -> None:
        if updates:
            self._headless_status.update({k: v for k, v in updates.items() if k in self._headless_status})
        if hasattr(self, "headless_progress_label"):
            parts = [f"{instance_id}: {self._headless_status.get(instance_id, '未接管')}" for instance_id in INSTANCE_IDS]
            self.headless_progress_label.setText("无头接管进度：" + " ｜ ".join(parts))

    def _request_handoff_headless(self) -> None:
        configs = self.browser_configs()
        updates = {
            item.instance_id: "接管中"
            for item in configs
            if isinstance(item, BrowserInstanceConfig) and item.instance_id in self._headless_status
        }
        self.update_headless_progress(updates)
        self.handoff_headless_requested.emit(configs)

    def _request_enter_room(self, room_index: int) -> None:
        ready_ids = {
            instance_id
            for instance_id, status in self._headless_status.items()
            if status == "无头大厅就绪" or "大厅就绪" in status
        }
        if not ready_ids:
            QMessageBox.warning(self, "进入房间", "当前没有已经完成无头接管并到达大厅的账号。")
            return
        configs = [
            item
            for item in self.browser_configs()
            if isinstance(item, BrowserInstanceConfig) and item.instance_id in ready_ids
        ]
        self.update_headless_progress({instance_id: f"进入房间 {room_index} 中" for instance_id in ready_ids})
        self.enter_room_requested.emit(room_index, configs)

    def _request_refresh_headless(self) -> None:
        configs = self.browser_configs()
        updates = {
            item.instance_id: "刷新状态中"
            for item in configs
            if isinstance(item, BrowserInstanceConfig) and item.instance_id in self._headless_status
        }
        self.update_headless_progress(updates)
        self.refresh_headless_requested.emit(configs)

    def _request_release_headless(self) -> None:
        answer = QMessageBox.question(
            self,
            "释放无头接管",
            "这会关闭当前隐藏运行的无头大厅或房间页面。确认释放吗？",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        configs = self.browser_configs()
        updates = {
            item.instance_id: "释放中"
            for item in configs
            if isinstance(item, BrowserInstanceConfig) and item.instance_id in self._headless_status
        }
        self.update_headless_progress(updates)
        self.release_headless_requested.emit(configs)

    def _configs_for_instance(self, instance_id: str) -> list[BrowserInstanceConfig]:
        return [
            item
            for item in self.browser_configs()
            if isinstance(item, BrowserInstanceConfig) and item.instance_id == instance_id
        ]

    def _request_restart_platform(self, instance_id: str) -> None:
        configs = self._configs_for_instance(instance_id)
        if not configs:
            QMessageBox.warning(self, "重启平台", f"{instance_id} 缺少平台配置。")
            return
        if not configs[0].login_url:
            QMessageBox.warning(self, "重启平台", f"{instance_id} 缺少登录页地址。")
            return
        self.update_headless_progress({instance_id: "重启中"})
        self.restart_platform_requested.emit(configs)

    def _request_fill_login_platform(self, instance_id: str) -> None:
        configs = self._configs_for_instance(instance_id)
        if not configs:
            QMessageBox.warning(self, "填写登录", f"{instance_id} 缺少平台配置。")
            return
        self.fill_current_login_requested.emit(configs)

    def _request_handoff_headless_platform(self, instance_id: str) -> None:
        configs = self._configs_for_instance(instance_id)
        if not configs:
            QMessageBox.warning(self, "接管大厅", f"{instance_id} 缺少平台配置。")
            return
        self.update_headless_progress({instance_id: "接管中"})
        self.handoff_headless_requested.emit(configs)

    def _request_enter_room_platform(self, instance_id: str, room_index: int) -> None:
        status = self._headless_status.get(instance_id, "")
        if "大厅就绪" not in status:
            QMessageBox.warning(self, "进入房间", f"{instance_id} 尚未完成无头接管并到达大厅。")
            return
        configs = self._configs_for_instance(instance_id)
        if not configs:
            QMessageBox.warning(self, "进入房间", f"{instance_id} 缺少平台配置。")
            return
        self.update_headless_progress({instance_id: f"进入房间 {room_index} 中"})
        self.enter_room_requested.emit(room_index, configs)

    def _request_release_headless_platform(self, instance_id: str) -> None:
        answer = QMessageBox.question(
            self,
            "释放单路无头",
            f"确认释放 {instance_id} 当前隐藏运行的无头大厅或房间页面吗？",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        configs = self._configs_for_instance(instance_id)
        if not configs:
            QMessageBox.warning(self, "释放单路无头", f"{instance_id} 缺少平台配置。")
            return
        self.update_headless_progress({instance_id: "释放中"})
        self.release_headless_requested.emit(configs)

    def _build_room_menu(self) -> QMenu:
        menu = QMenu(self)
        for room_index in (1, 2, 3, 4):
            menu.addAction(
                f"进入房间 {room_index}",
                lambda checked=False, room_index=room_index: self._request_enter_room(room_index),
            )
        return menu

    def _build_room_menu_for_instance(self, instance_id: str) -> QMenu:
        menu = QMenu(self)
        for room_index in (1, 2, 3, 4):
            menu.addAction(
                f"房间 {room_index}",
                lambda checked=False, item_id=instance_id, room_index=room_index: self._request_enter_room_platform(
                    item_id,
                    room_index,
                ),
            )
        return menu

    def _build_proxy_section(self) -> QFrame:
        self._build_legacy_proxy_widgets()
        self._build_platform_config_dialog()

        section = self._section_frame("四平台群控台")
        body = section.layout()

        header = QHBoxLayout()
        header.addWidget(QLabel("平台启动"))
        header.addStretch(1)
        config_button = QPushButton("平台配置")
        config_button.clicked.connect(lambda: self._open_platform_config_dialog())
        header.addWidget(config_button)
        body.addLayout(header)

        self.platform_name_inputs: dict[str, QLineEdit] = {}
        self.platform_proxy_status_labels: dict[str, QLabel] = {}
        self.platform_account_status_labels: dict[str, QLabel] = {}
        self.platform_deposit_buttons: dict[str, QPushButton] = {}
        self.platform_withdraw_buttons: dict[str, QPushButton] = {}
        cards = QGridLayout()
        cards.setHorizontalSpacing(10)
        cards.setVerticalSpacing(10)
        for index, instance_id in enumerate(INSTANCE_IDS):
            card = self._build_platform_launch_card(instance_id, index + 1)
            cards.addWidget(card, index // 2, index % 2)
        body.addLayout(cards)

        launch_all_button = QPushButton("启动全部平台")
        launch_all_button.clicked.connect(lambda: self.launch_browser_requested.emit(self.browser_configs()))
        body.addWidget(launch_all_button)

        handoff_buttons = QHBoxLayout()
        handoff_button = QPushButton("一键接管大厅")
        handoff_button.clicked.connect(self._request_handoff_headless)
        room_entry_button = QPushButton("进入房间")
        room_entry_button.setMenu(self._build_room_menu())
        handoff_buttons.addWidget(handoff_button)
        handoff_buttons.addWidget(room_entry_button)
        body.addLayout(handoff_buttons)

        headless_manage_buttons = QHBoxLayout()
        refresh_headless_button = QPushButton("刷新无头状态")
        refresh_headless_button.clicked.connect(self._request_refresh_headless)
        release_headless_button = QPushButton("释放无头接管")
        release_headless_button.clicked.connect(self._request_release_headless)
        headless_manage_buttons.addWidget(refresh_headless_button)
        headless_manage_buttons.addWidget(release_headless_button)
        body.addLayout(headless_manage_buttons)

        self.headless_progress_label = QLabel()
        self.headless_progress_label.setObjectName("headlessProgressLabel")
        self.headless_progress_label.setWordWrap(True)
        body.addWidget(self.headless_progress_label)
        return section

    def _build_legacy_proxy_widgets(self) -> None:
        self.platform_name_combo = QComboBox(self)
        self.platform_name_combo.setEditable(True)
        self.platform_name_combo.setPlaceholderText("输入或选择平台名称")
        self.platform_load_button = QPushButton("载入平台配置", self)
        self.platform_save_button = QPushButton("保存平台配置", self)
        self.platform_delete_button = QPushButton("删除平台配置", self)
        self.platform_load_button.clicked.connect(self._on_load_platform_profile)
        self.platform_save_button.clicked.connect(self._on_save_platform_profile)
        self.platform_delete_button.clicked.connect(self._on_delete_platform_profile)
        for widget in (
            self.platform_name_combo,
            self.platform_load_button,
            self.platform_save_button,
            self.platform_delete_button,
        ):
            widget.hide()

        self.proxy_table = QTableWidget(len(INSTANCE_IDS), 5)
        self.proxy_table.setObjectName("proxyConfigTable")
        self.proxy_table.setHorizontalHeaderLabels(("实例", "主机", "端口", "用户", "密码"))
        self.proxy_table.verticalHeader().setVisible(False)
        self.proxy_table.verticalHeader().setDefaultSectionSize(38)
        self.proxy_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.proxy_table.setFixedHeight(190)
        for row, instance_id in enumerate(INSTANCE_IDS):
            self.proxy_table.setItem(row, 0, self._readonly_item(instance_id))
            for col, placeholder in enumerate(("代理主机", "端口", "用户名", "密码"), start=1):
                editor = QLineEdit()
                editor.setPlaceholderText(placeholder)
                if col == 4:
                    editor.setEchoMode(QLineEdit.EchoMode.Password)
                self.proxy_table.setCellWidget(row, col, editor)
        self.proxy_table.setParent(self)
        self.proxy_table.hide()

        url_grid = QGridLayout()
        self.login_url_input = QLineEdit()
        self.login_url_input.setPlaceholderText("登录页地址")
        self.target_url_input = QLineEdit()
        self.target_url_input.setPlaceholderText("目标游戏页地址")
        url_grid.addWidget(QLabel("登录页"), 0, 0)
        url_grid.addWidget(self.login_url_input, 0, 1)
        url_grid.addWidget(QLabel("目标页"), 1, 0)
        url_grid.addWidget(self.target_url_input, 1, 1)
        self.login_url_input.setParent(self)
        self.target_url_input.setParent(self)
        self.login_url_input.hide()
        self.target_url_input.hide()

    def _build_platform_config_dialog(self) -> None:
        self.platform_fields: dict[str, dict[str, QLineEdit]] = {}
        self.platform_config_dialog = QDialog(self)
        self.platform_config_dialog.setWindowTitle("平台二级配置")
        self.platform_config_dialog.resize(560, 620)

        dialog_layout = QVBoxLayout(self.platform_config_dialog)
        dialog_layout.setContentsMargins(12, 12, 12, 12)
        dialog_layout.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel("平台配置")
        title.setObjectName("controllerSectionTitle")
        save_button = QPushButton("保存")
        save_button.clicked.connect(self._save_platform_slots)
        apply_button = QPushButton("应用当前配置")
        apply_button.clicked.connect(self._apply_current_platform_settings)
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(apply_button)
        header.addWidget(save_button)
        dialog_layout.addLayout(header)

        selector = QGridLayout()
        selector.setHorizontalSpacing(8)
        selector.setVerticalSpacing(8)
        self.platform_config_stack = QStackedWidget()
        self.platform_edit_buttons: dict[str, QPushButton] = {}
        for index, instance_id in enumerate(INSTANCE_IDS):
            edit_button = QPushButton(f"编辑 平台 {index + 1}")
            edit_button.clicked.connect(lambda checked=False, i=index: self.platform_config_stack.setCurrentIndex(i))
            self.platform_edit_buttons[instance_id] = edit_button
            selector.addWidget(edit_button, index // 2, index % 2)
            self.platform_config_stack.addWidget(self._build_platform_config_card(instance_id, index + 1))
        dialog_layout.addLayout(selector)
        dialog_layout.addWidget(self.platform_config_stack, 1)

    def _build_platform_config_card(self, instance_id: str, platform_index: int) -> QFrame:
        card = QFrame()
        card.setObjectName("controllerSection")
        layout = QGridLayout(card)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setHorizontalSpacing(10)
        layout.setVerticalSpacing(8)

        fields: dict[str, QLineEdit] = {}
        rows = (
            ("name", "平台名称", f"平台 {platform_index}"),
            ("login_url", "平台网址", "https://example.com"),
            ("target_url", "目标房间", "已配置房间入口"),
            ("target_room", "房间备注", "房间 1 / 房间 2"),
            ("proxy_host", "代理主机", "proxy-host-01"),
            ("proxy_port", "代理端口", "8080"),
            ("proxy_username", "代理账号", "proxy-user-01"),
            ("proxy_password", "代理密码", "********"),
            ("account_username", "平台账号", "平台登录账号"),
            ("account_password", "平台密码", "********"),
        )
        for row, (key, label, placeholder) in enumerate(rows):
            editor = QLineEdit()
            editor.setPlaceholderText(placeholder)
            if key.endswith("password"):
                editor.setEchoMode(QLineEdit.EchoMode.Password)
            editor.textChanged.connect(lambda _text, item_id=instance_id: self._on_platform_field_changed(item_id))
            fields[key] = editor
            layout.addWidget(QLabel(label), row, 0)
            layout.addWidget(editor, row, 1)
        self.platform_fields[instance_id] = fields
        return card

    def _build_platform_launch_card(self, instance_id: str, platform_index: int) -> QFrame:
        card = QFrame()
        card.setObjectName("controllerSection")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        header = QHBoxLayout()
        label = QLabel(f"平台 {platform_index}")
        label.setObjectName("controllerSectionTitle")
        edit_button = QPushButton("编辑")
        edit_button.clicked.connect(lambda checked=False, item_id=instance_id: self._open_platform_config_dialog(item_id))
        header.addWidget(label)
        header.addStretch(1)
        header.addWidget(edit_button)
        layout.addLayout(header)

        name_input = QLineEdit()
        name_input.setPlaceholderText(f"自定义平台名称 {platform_index}")
        name_input.textChanged.connect(lambda text, item_id=instance_id: self._set_platform_field_text(item_id, "name", text))
        self.platform_name_inputs[instance_id] = name_input
        layout.addWidget(name_input)

        status_row = QHBoxLayout()
        proxy_label = QLabel("代理：未绑定")
        account_label = QLabel("账号：未绑定")
        proxy_label.setObjectName("platformStatusLabel")
        account_label.setObjectName("platformStatusLabel")
        self.platform_proxy_status_labels[instance_id] = proxy_label
        self.platform_account_status_labels[instance_id] = account_label
        status_row.addWidget(proxy_label)
        status_row.addWidget(account_label)
        layout.addLayout(status_row)

        primary_actions = QHBoxLayout()
        launch_button = QPushButton("启动/重启")
        launch_button.setToolTip("只重启这一平台，其他平台保持运行")
        launch_button.clicked.connect(
            lambda checked=False, item_id=instance_id: self._request_restart_platform(item_id)
        )
        fill_button = QPushButton("填写登录")
        fill_button.clicked.connect(
            lambda checked=False, item_id=instance_id: self._request_fill_login_platform(item_id)
        )
        primary_actions.addWidget(launch_button)
        primary_actions.addWidget(fill_button)
        layout.addLayout(primary_actions)

        headless_actions = QHBoxLayout()
        handoff_button = QPushButton("接管大厅")
        handoff_button.clicked.connect(
            lambda checked=False, item_id=instance_id: self._request_handoff_headless_platform(item_id)
        )
        room_button = QPushButton("进房")
        room_button.setMenu(self._build_room_menu_for_instance(instance_id))
        headless_actions.addWidget(handoff_button)
        headless_actions.addWidget(room_button)
        layout.addLayout(headless_actions)

        wallet_actions = QHBoxLayout()
        deposit_button = QPushButton("存款")
        deposit_button.clicked.connect(
            lambda checked=False, item_id=instance_id: self._show_pending_feature(item_id, "存款")
        )
        withdraw_button = QPushButton("提现")
        withdraw_button.clicked.connect(
            lambda checked=False, item_id=instance_id: self._show_pending_feature(item_id, "提现")
        )
        self.platform_deposit_buttons[instance_id] = deposit_button
        self.platform_withdraw_buttons[instance_id] = withdraw_button
        wallet_actions.addWidget(deposit_button)
        wallet_actions.addWidget(withdraw_button)
        layout.addLayout(wallet_actions)

        release_button = QPushButton("释放无头")
        release_button.clicked.connect(
            lambda checked=False, item_id=instance_id: self._request_release_headless_platform(item_id)
        )
        layout.addWidget(release_button)
        return card

    def _show_pending_feature(self, instance_id: str, action: str) -> None:
        QMessageBox.information(self, action, f"{instance_id} {action}功能待实现")

    def _open_platform_config_dialog(self, instance_id: str | None = None) -> None:
        if instance_id in INSTANCE_IDS:
            self.platform_config_stack.setCurrentIndex(INSTANCE_IDS.index(instance_id))
        self.platform_config_dialog.show()
        self.platform_config_dialog.raise_()
        self.platform_config_dialog.activateWindow()

    def _request_launch_platform(self, instance_id: str) -> None:
        configs = [
            item
            for item in self.browser_configs()
            if isinstance(item, BrowserInstanceConfig) and item.instance_id == instance_id
        ]
        self.launch_browser_requested.emit(configs)

    def _on_platform_field_changed(self, instance_id: str) -> None:
        if instance_id not in INSTANCE_IDS:
            return
        name = self._platform_field_text(instance_id, "name")
        name_input = getattr(self, "platform_name_inputs", {}).get(instance_id)
        if name_input is not None and name_input.text() != name:
            name_input.blockSignals(True)
            name_input.setText(name)
            name_input.blockSignals(False)
        self._update_platform_status(instance_id)

    def _update_platform_status(self, instance_id: str) -> None:
        proxy_bound = bool(
            self._platform_field_text(instance_id, "proxy_host")
            or self._legacy_proxy_text(instance_id, 1)
        )
        account_bound = bool(
            self._platform_field_text(instance_id, "account_username")
            or self._legacy_account_text(instance_id, 1)
        )
        proxy_label = getattr(self, "platform_proxy_status_labels", {}).get(instance_id)
        account_label = getattr(self, "platform_account_status_labels", {}).get(instance_id)
        if proxy_label is not None:
            proxy_label.setText("代理：已绑定" if proxy_bound else "代理：未绑定")
        if account_label is not None:
            account_label.setText("账号：已绑定" if account_bound else "账号：未绑定")

    def _apply_current_platform_settings(self) -> None:
        self.proxy_snapshot_changed.emit(self.proxy_snapshot())
        self.account_snapshot_changed.emit(self.account_snapshot())

    def _save_platform_slots(self) -> None:
        self._write_platform_profiles()
        QMessageBox.information(self, "平台配置", "四个平台配置已保存。")

    def _selected_platform_name(self) -> str:
        text = self.platform_name_combo.currentText().strip()
        return text

    def _sync_platform_profiles_combo(self, keep_current: str | None = None) -> None:
        current = keep_current if keep_current is not None else self._selected_platform_name()
        self.platform_name_combo.blockSignals(True)
        self.platform_name_combo.clear()
        for name in sorted(self._platform_profiles):
            self.platform_name_combo.addItem(name)
        if current and current in self._platform_profiles:
            self.platform_name_combo.setCurrentText(current)
        self.platform_name_combo.setEditText(current)
        self.platform_name_combo.blockSignals(False)

    def _platform_profile_payload(self) -> dict[str, Any]:
        if hasattr(self, "platform_fields"):
            slots = {
                instance_id: self._slot_profile_payload(instance_id)
                for instance_id in INSTANCE_IDS
            }
            return {"platform_slots": slots}
        payload: dict[str, Any] = {
            "login_url": self.login_url_input.text().strip(),
            "target_url": self.target_url_input.text().strip(),
            "instances": {},
        }
        for row, instance_id in enumerate(INSTANCE_IDS):
            payload["instances"][instance_id] = {
                "host": self._cell_text(self.proxy_table, row, 1),
                "port": self._cell_text(self.proxy_table, row, 2),
                "username": self._cell_text(self.proxy_table, row, 3),
                "password": self._cell_text(self.proxy_table, row, 4),
            }
        return payload

    def _apply_platform_profile(self, profile: dict[str, Any]) -> None:
        slots = profile.get("platform_slots")
        if isinstance(slots, dict) and hasattr(self, "platform_fields"):
            for instance_id in INSTANCE_IDS:
                item = slots.get(instance_id, {})
                if isinstance(item, dict):
                    self._apply_slot_profile(instance_id, item)
            return
        self.login_url_input.setText(str(profile.get("login_url") or ""))
        self.target_url_input.setText(str(profile.get("target_url") or ""))
        instances = profile.get("instances", {})
        if isinstance(instances, dict):
            for row, instance_id in enumerate(INSTANCE_IDS):
                item = instances.get(instance_id, {})
                if isinstance(item, dict):
                    self._set_cell_text(self.proxy_table, row, 1, item.get("host", ""))
                    self._set_cell_text(self.proxy_table, row, 2, item.get("port", ""))
                    self._set_cell_text(self.proxy_table, row, 3, item.get("username", ""))
                    self._set_cell_text(self.proxy_table, row, 4, item.get("password", ""))

    def _slot_profile_payload(self, instance_id: str) -> dict[str, str]:
        fields = getattr(self, "platform_fields", {}).get(instance_id, {})
        return {
            key: editor.text().strip()
            for key, editor in fields.items()
            if isinstance(editor, QLineEdit)
        }

    def _apply_slot_profile(self, instance_id: str, profile: dict[str, Any]) -> None:
        for key, editor in getattr(self, "platform_fields", {}).get(instance_id, {}).items():
            if isinstance(editor, QLineEdit):
                editor.setText(str(profile.get(key) or ""))
        self._update_platform_status(instance_id)

    def _platform_field_text(self, instance_id: str, key: str) -> str:
        editor = getattr(self, "platform_fields", {}).get(instance_id, {}).get(key)
        if isinstance(editor, QLineEdit):
            return editor.text().strip()
        return ""

    def _set_platform_field_text(self, instance_id: str, key: str, value: str) -> None:
        editor = getattr(self, "platform_fields", {}).get(instance_id, {}).get(key)
        if not isinstance(editor, QLineEdit) or editor.text() == value:
            return
        editor.setText(value)

    def _legacy_proxy_text(self, instance_id: str, col: int) -> str:
        if instance_id not in INSTANCE_IDS:
            return ""
        return self._cell_text(self.proxy_table, INSTANCE_IDS.index(instance_id), col)

    def _legacy_account_text(self, instance_id: str, col: int) -> str:
        if instance_id not in INSTANCE_IDS:
            return ""
        return self._cell_text(self.account_table, INSTANCE_IDS.index(instance_id), col)

    def _set_cell_text(self, table: QTableWidget, row: int, col: int, value: object) -> None:
        widget = table.cellWidget(row, col)
        if isinstance(widget, QLineEdit):
            widget.setText("" if value is None else str(value))

    def _load_platform_profiles(self) -> None:
        try:
            if self._platform_profile_path.exists():
                raw = json.loads(self._platform_profile_path.read_text(encoding="utf-8"))
                loaded = raw.get("platforms") if isinstance(raw, dict) else None
                if isinstance(loaded, dict):
                    self._platform_profiles = {
                        str(name): profile if isinstance(profile, dict) else {}
                        for name, profile in loaded.items()
                    }
                slots = raw.get("platform_slots") if isinstance(raw, dict) else None
                if isinstance(slots, dict) and hasattr(self, "platform_fields"):
                    for instance_id in INSTANCE_IDS:
                        item = slots.get(instance_id, {})
                        if isinstance(item, dict):
                            self._apply_slot_profile(instance_id, item)
            self._sync_platform_profiles_combo()
        except Exception:
            self._platform_profiles = {}
            self._sync_platform_profiles_combo()

    def _write_platform_profiles(self) -> None:
        data: dict[str, Any] = {"platforms": self._platform_profiles}
        if hasattr(self, "platform_fields"):
            data["platform_slots"] = {
                instance_id: self._slot_profile_payload(instance_id)
                for instance_id in INSTANCE_IDS
            }
        self._platform_profile_path.parent.mkdir(parents=True, exist_ok=True)
        self._platform_profile_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _on_load_platform_profile(self) -> None:
        name = self._selected_platform_name()
        if not name:
            QMessageBox.warning(self, "平台配置", "请输入或选择平台名称。")
            return
        profile = self._platform_profiles.get(name)
        if not profile:
            QMessageBox.warning(self, "平台配置", "平台不存在，请先保存。")
            return
        self._apply_platform_profile(profile)
        self.proxy_snapshot_changed.emit(self.proxy_snapshot())

    def _on_save_platform_profile(self) -> None:
        name = self._selected_platform_name()
        if not name:
            QMessageBox.warning(self, "平台配置", "请输入平台名称后再保存。")
            return
        profile = self._platform_profile_payload()
        self._platform_profiles[name] = profile
        self._write_platform_profiles()
        self._sync_platform_profiles_combo(keep_current=name)
        QMessageBox.information(self, "平台配置", f"平台「{name}」已保存到 artifacts。")

    def _on_delete_platform_profile(self) -> None:
        name = self._selected_platform_name()
        if not name:
            QMessageBox.warning(self, "平台配置", "请输入或选择平台名称。")
            return
        if name not in self._platform_profiles:
            QMessageBox.warning(self, "平台配置", "平台不存在，无需删除。")
            return
        self._platform_profiles.pop(name, None)
        self._write_platform_profiles()
        self._sync_platform_profiles_combo()
        QMessageBox.information(self, "平台配置", f"平台「{name}」已删除。")

    def _build_legacy_account_widgets(self) -> None:
        self.account_table = QTableWidget(len(INSTANCE_IDS), 5)
        self.account_table.setObjectName("accountConfigTable")
        self.account_table.setHorizontalHeaderLabels(("实例", "登录账号", "登录密码", "浏览器凭据", "账号备注"))
        self.account_table.verticalHeader().setVisible(False)
        self.account_table.verticalHeader().setDefaultSectionSize(38)
        self.account_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.account_table.setFixedHeight(190)
        for row, instance_id in enumerate(INSTANCE_IDS):
            self.account_table.setItem(row, 0, self._readonly_item(instance_id))
            for col, placeholder in enumerate(("登录账号", "登录密码", "浏览器凭据", "账号备注"), start=1):
                editor = QLineEdit()
                editor.setPlaceholderText(placeholder)
                if col in (2, 3):
                    editor.setEchoMode(QLineEdit.EchoMode.Password)
                self.account_table.setCellWidget(row, col, editor)
        self.account_table.setParent(self)
        self.account_table.hide()

    def _build_test_case_section(self) -> QFrame:
        section = self._section_frame("执行控制")
        body = section.layout()

        action_buttons = QGridLayout()
        action_buttons.setHorizontalSpacing(8)
        action_buttons.setVerticalSpacing(8)
        self.start_execute_button = QPushButton("启动执行")
        self.start_execute_button.setToolTip("启动策略调度，执行前仍会先走账号状态和下注窗口检查。")
        self.start_execute_button.clicked.connect(self.start_execution_requested.emit)
        self.pause_execute_button = QPushButton("暂停")
        self.pause_execute_button.setToolTip("暂停后续调度，保留当前日志和账号状态。")
        self.pause_execute_button.clicked.connect(self.pause_execution_requested.emit)
        self.stop_execute_button = QPushButton("停止")
        self.stop_execute_button.setToolTip("停止整组方案执行，不关闭已经启动的平台。")
        self.stop_execute_button.clicked.connect(self.stop_execution_requested.emit)
        clear_button = QPushButton("清空日志")
        clear_button.clicked.connect(lambda: self.algorithm_log.clear())
        action_buttons.addWidget(self.start_execute_button, 0, 0)
        action_buttons.addWidget(self.pause_execute_button, 0, 1)
        action_buttons.addWidget(self.stop_execute_button, 0, 2)
        action_buttons.addWidget(clear_button, 0, 3)
        body.addLayout(action_buttons)

        self.hedge_model = QComboBox()
        self.hedge_model.addItems(
            (
                "选项A：主闲 / 副庄对冲",
                "选项B：主庄 / 副闲对冲",
                "选项C：多向离散混合测试",
            )
        )
        self.hedge_model.currentTextChanged.connect(self.hedge_model_changed.emit)

        self.amount_input = QSpinBox()
        self.amount_input.setRange(80, 250)
        self.amount_input.setSingleStep(10)
        self.amount_input.setValue(100)
        self.amount_input.valueChanged.connect(self._on_amount_changed)
        self.amount_input.hide()

        self.decomposition_preview = QLabel("")
        self.decomposition_preview.setObjectName("decompositionPreview")
        self.decomposition_preview.hide()

        user_options = QGridLayout()
        user_options.setHorizontalSpacing(8)
        user_options.setVerticalSpacing(8)
        self.main_account_combo = QComboBox()
        self.main_account_combo.addItems(("平台1 / a1", "平台2 / a2", "平台3 / a3", "平台4 / a4"))
        self.auto_sub_checkbox = QCheckBox("副号从剩余可用账号自动选择")
        self.main_account_combo.currentIndexChanged.connect(lambda _index: self.update_decomposition_preview([]))
        self.auto_sub_checkbox.setChecked(True)
        self.auto_sub_checkbox.setEnabled(False)
        self.excluded_account_checks: dict[str, QCheckBox] = {}
        exclude_row = QHBoxLayout()
        for index, instance_id in enumerate(INSTANCE_IDS, start=1):
            checkbox = QCheckBox(f"剔除平台{index}")
            self.excluded_account_checks[instance_id] = checkbox
            checkbox.stateChanged.connect(lambda _state: self.update_decomposition_preview([]))
            exclude_row.addWidget(checkbox)
        self.addon_four_mode = QComboBox()
        self.addon_four_mode.addItems(("关闭补4", "庄补4", "闲补4", "随机庄/闲补4"))
        self.addon_four_frequency = QComboBox()
        self.addon_four_frequency.addItems(("低频随机", "每3局最多1次", "每5局最多1次", "每局都补"))
        user_options.addWidget(QLabel("主号权限"), 0, 0)
        user_options.addWidget(self.main_account_combo, 0, 1)
        user_options.addWidget(QLabel("副号权限"), 1, 0)
        user_options.addWidget(self.auto_sub_checkbox, 1, 1)
        user_options.addWidget(QLabel("账号剔除"), 2, 0)
        user_options.addLayout(exclude_row, 2, 1)
        user_options.addWidget(QLabel("补4规则"), 3, 0)
        addon_row = QHBoxLayout()
        addon_row.addWidget(self.addon_four_mode)
        addon_row.addWidget(self.addon_four_frequency)
        user_options.addLayout(addon_row, 3, 1)
        body.addLayout(user_options)

        profit_grid = QGridLayout()
        profit_grid.setHorizontalSpacing(8)
        profit_grid.setVerticalSpacing(8)
        self.platform_profit_loss_labels: dict[str, QLabel] = {}
        self._profit_loss_baselines: dict[str, float] = {}
        self._runtime_ledger_pnl: dict[str, float] = {}
        for index, instance_id in enumerate(INSTANCE_IDS, start=1):
            label = self._value_label(f"{instance_id}_profit_loss")
            label.setText("0.00")
            self.platform_profit_loss_labels[instance_id] = label
            self._add_metric(profit_grid, (index - 1) // 2, (index - 1) % 2, f"平台{index}当前盈亏", label)
        body.addLayout(profit_grid)

        self.algorithm_log = QTextEdit()
        self.algorithm_log.setObjectName("algorithmExecutionLog")
        self.algorithm_log.setReadOnly(True)
        self.algorithm_log.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.algorithm_log.setMinimumHeight(260)
        self.algorithm_log.setPlainText(
            "执行日志已就绪。\n"
            "拆分预览、下注前检测、账号剔除原因、下注结果、结算结果和补救动作会写入这里。"
        )
        body.addWidget(self.algorithm_log, 1)
        self.update_decomposition_preview([100])
        return section

    def execution_config(self) -> HedgeExecutionConfig:
        main_account_index = max(0, self.main_account_combo.currentIndex())
        main_account_id = f"a{main_account_index + 1}"

        excluded = [
            instance_id
            for instance_id, checkbox in self.excluded_account_checks.items()
            if checkbox.isChecked() and instance_id != main_account_id
        ]

        mode_map = {
            "关闭补4": AddonFourMode.OFF,
            "庄补4": AddonFourMode.BANKER,
            "闲补4": AddonFourMode.PLAYER,
            "随机庄/闲补4": AddonFourMode.RANDOM,
        }
        frequency_map = {
            "低频随机": AddonFourFrequency.LOW_RANDOM,
            "每3局最多1次": AddonFourFrequency.EVERY_3_ROUNDS,
            "每5局最多1次": AddonFourFrequency.EVERY_5_ROUNDS,
            "每局都补": AddonFourFrequency.EVERY_ROUND,
        }
        mode_text = self.addon_four_mode.currentText().strip()
        frequency_text = self.addon_four_frequency.currentText().strip()
        frequency = frequency_map.get(frequency_text, AddonFourFrequency.LOW_RANDOM)
        if frequency_text not in frequency_map:
            if "3" in frequency_text:
                frequency = AddonFourFrequency.EVERY_3_ROUNDS
            elif "5" in frequency_text:
                frequency = AddonFourFrequency.EVERY_5_ROUNDS
            elif "每局" in frequency_text:
                frequency = AddonFourFrequency.EVERY_ROUND

        return HedgeExecutionConfig(
            main_account_id=main_account_id,
            excluded_account_ids=tuple(excluded),
            hedge_model=self.hedge_model.currentText().strip(),
            main_amount_min=int(self.amount_input.value() if hasattr(self, "amount_input") else 100),
            main_amount_max=int(self.amount_input.value() if hasattr(self, "amount_input") else 100),
            addon_four_mode=mode_map.get(mode_text, AddonFourMode.OFF),
            addon_four_frequency=frequency,
        )

    def _value_label(self, object_name: str) -> QLabel:
        label = QLabel("-")
        label.setObjectName(object_name)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        label.setWordWrap(True)
        return label

    def _add_metric(self, layout: QGridLayout, row: int, col: int, title: str, value: QLabel) -> None:
        box = QFrame()
        box.setObjectName("metricBox")
        box_layout = QVBoxLayout(box)
        box_layout.setContentsMargins(8, 6, 8, 6)
        name = QLabel(title)
        name.setObjectName("metricName")
        box_layout.addWidget(name)
        box_layout.addWidget(value)
        layout.addWidget(box, row, col)

    def _section_frame(self, title: str) -> QFrame:
        section = QFrame()
        section.setObjectName("controllerSection")
        layout = QVBoxLayout(section)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        label = QLabel(title)
        label.setObjectName("controllerSectionTitle")
        layout.addWidget(label)
        return section

    def _on_amount_changed(self, amount: int) -> None:
        self.amount_changed.emit(amount)

    def update_decomposition_preview(self, chips: list[int]) -> None:
        amount = int(self.amount_input.value()) if hasattr(self, "amount_input") else sum(chips)
        split = self._draft_sub_account_split(amount)
        split_text = " / ".join(f"{item_id} {value}" for item_id, value in split.items())
        if self._current_excluded_account_ids():
            split_text = f"{split_text} | excluded {','.join(self._current_excluded_account_ids())}"
        preview = f"拆分预览：主号 {amount} ｜ 副号合计 {sum(split.values())} ｜ {split_text}"
        if hasattr(self, "decomposition_preview"):
            self.decomposition_preview.setText(preview)
        if hasattr(self, "algorithm_log"):
            self.algorithm_log.append(f"[拆分预览] {preview}")

    def _current_main_account_id(self) -> str:
        index = max(0, self.main_account_combo.currentIndex()) if hasattr(self, "main_account_combo") else 0
        return f"a{index + 1}"

    def _current_excluded_account_ids(self) -> tuple[str, ...]:
        main_account_id = self._current_main_account_id()
        checks = getattr(self, "excluded_account_checks", {})
        return tuple(
            instance_id
            for instance_id, checkbox in checks.items()
            if checkbox.isChecked() and instance_id != main_account_id
        )

    def _draft_sub_account_split(self, amount: int) -> dict[str, int]:
        main_account_id = self._current_main_account_id()
        excluded = set(self._current_excluded_account_ids())
        sub_ids = tuple(
            instance_id
            for instance_id in INSTANCE_IDS
            if instance_id != main_account_id and instance_id not in excluded
        )
        if not sub_ids:
            return {}
        unit = 10 if amount % 10 == 0 else 1
        units = amount // unit
        base_units = units // len(sub_ids)
        remainder_units = units - base_units * len(sub_ids)
        split: dict[str, int] = {}
        for index, item_id in enumerate(sub_ids):
            split[item_id] = (base_units + (1 if index < remainder_units else 0)) * unit
        return split

    def _append_algorithm_placeholder(self, action: str, detail: str) -> None:
        if hasattr(self, "algorithm_log"):
            self.algorithm_log.append(f"[{action}] {detail}")

    def update_runtime_ledger_profit_loss(self, pnl_by_account: dict[str, float]) -> None:
        self._runtime_ledger_pnl = {
            str(instance_id): float(value)
            for instance_id, value in pnl_by_account.items()
            if str(instance_id) in INSTANCE_IDS
        }

    def format_hedge_execution_event(self, event: object) -> str:
        if not isinstance(event, ExecutionEvent):
            return "执行编排器事件：未解析类型"

        payload = event.as_dict()
        event_type = payload.get("event_type") or ""
        event_type_text = str(event_type)
        summary = payload.get("safe_summary") or {}
        message = str(payload.get("message") or "").strip() or "状态更新"
        round_id = summary.get("round_id") or ""

        extras: list[str] = []
        if round_id:
            extras.append(f"局号={round_id}")

        if event_type == ExecutionEventType.PLAN_GENERATED or event_type == ExecutionEventType.PLAN_GENERATED.value:
            main_account = summary.get("main_account_id") or ""
            main_amount = summary.get("main_amount")
            addon = summary.get("addon_four") or {}
            if main_account:
                extras.append(f"主号={main_account}")
            if main_amount is not None:
                extras.append(f"主号金额={main_amount}")
            if isinstance(addon, dict) and addon.get("enabled"):
                extras.append("补4动作=已触发")
                if addon.get("side"):
                    extras.append(f"补4方向={addon['side']}")
            elif isinstance(addon, dict):
                extras.append("补4动作=未触发")
        elif event_type == ExecutionEventType.ADDON_FOUR_DECIDED or event_type == ExecutionEventType.ADDON_FOUR_DECIDED.value:
            addon = summary.get("addon") or {}
            if isinstance(addon, dict):
                if addon.get("enabled"):
                    extras.append("额外风险动作=补4")
                    extras.append(f"补4原因={addon.get('reason','')}")
                    if addon.get("side"):
                        extras.append(f"补4方向={addon['side']}")
                else:
                    extras.append("补4动作=未启用")
        elif event_type == ExecutionEventType.ACCOUNT_EXCLUDED or event_type == ExecutionEventType.ACCOUNT_EXCLUDED.value:
            account_id = summary.get("account_id") or ""
            if account_id:
                extras.append(f"排除账号={account_id}")
        elif event_type == ExecutionEventType.EXECUTION_BLOCKED or event_type == ExecutionEventType.EXECUTION_BLOCKED.value:
            reason = summary.get("reason") or summary.get("blocked_accounts") or summary.get("missing_instances")
            if reason:
                extras.append(f"原因={reason}")

        detail = "，".join(extras) if extras else "无额外详情"
        return f"{event_type_text}｜{message}｜{detail}"

    def update_platform_profit_loss(self, snapshot: TemporalStateSnapshot) -> None:
        if snapshot.instance_id not in INSTANCE_IDS:
            return
        labels = getattr(self, "platform_profit_loss_labels", {})
        label = labels.get(snapshot.instance_id)
        if label is None:
            return
        value = getattr(self, "_runtime_ledger_pnl", {}).get(snapshot.instance_id)
        if value is None:
            value = self._profit_loss_from_summary(snapshot.safe_summary)
        if value is None:
            current_balance = self._parse_amount(snapshot.ocr_balance)
            if current_balance is not None:
                baseline = self._profit_loss_baselines.setdefault(snapshot.instance_id, current_balance)
                value = current_balance - baseline
        if value is None:
            label.setText("未识别")
            label.setProperty("tone", "warning")
        else:
            label.setText(f"{value:+.2f}")
            label.setProperty("tone", "ok" if value >= 0 else "danger")
        label.style().unpolish(label)
        label.style().polish(label)

    @staticmethod
    def _profit_loss_from_summary(summary: dict[str, Any]) -> float | None:
        for key in (
            "profit_loss",
            "current_profit_loss",
            "platform_profit_loss",
            "daily_pnl",
            "pnl",
            "net_profit",
            "win_loss",
        ):
            value = summary.get(key)
            parsed = GlobalControllerPanel._parse_amount(value)
            if parsed is not None:
                return parsed
        return None

    @staticmethod
    def _parse_amount(value: object) -> float | None:
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value or "").strip()
        if not text:
            return None
        match = re.search(r"[-+]?\d+(?:,\d{3})*(?:\.\d+)?|[-+]?\d+(?:\.\d+)?", text)
        if not match:
            return None
        try:
            return float(match.group(0).replace(",", ""))
        except ValueError:
            return None

    def proxy_snapshot(self) -> dict[str, dict[str, str]]:
        snapshot: dict[str, dict[str, str]] = {}
        for row, instance_id in enumerate(INSTANCE_IDS):
            password = self._platform_field_text(instance_id, "proxy_password") or self._cell_text(self.proxy_table, row, 4)
            snapshot[instance_id] = {
                "host": self._platform_field_text(instance_id, "proxy_host") or self._cell_text(self.proxy_table, row, 1),
                "port": self._platform_field_text(instance_id, "proxy_port") or self._cell_text(self.proxy_table, row, 2),
                "username": self._platform_field_text(instance_id, "proxy_username") or self._cell_text(self.proxy_table, row, 3),
                "password": "***" if password else "",
            }
        return snapshot

    def account_snapshot(self) -> dict[str, dict[str, str]]:
        snapshot: dict[str, dict[str, str]] = {}
        for row, instance_id in enumerate(INSTANCE_IDS):
            password = self._platform_field_text(instance_id, "account_password") or self._cell_text(self.account_table, row, 2)
            snapshot[instance_id] = {
                "username": self._platform_field_text(instance_id, "account_username") or self._cell_text(self.account_table, row, 1),
                "password": "***" if password else "",
                "cookie_or_token": "***" if self._cell_text(self.account_table, row, 3) else "",
                "account_note": self._cell_text(self.account_table, row, 4),
            }
        return snapshot

    def browser_configs(self) -> list[BrowserInstanceConfig]:
        configs: list[BrowserInstanceConfig] = []
        for row, instance_id in enumerate(INSTANCE_IDS):
            login_url = _normalize_navigation_url(
                self._platform_field_text(instance_id, "login_url") or self.login_url_input.text().strip()
            )
            target_url = _normalize_navigation_url(
                self._platform_field_text(instance_id, "target_url") or self.target_url_input.text().strip()
            )
            configs.append(
                BrowserInstanceConfig(
                    instance_id=instance_id,
                    login_url=login_url,
                    target_url=target_url,
                    proxy=ProxyConfig(
                        host=self._platform_field_text(instance_id, "proxy_host") or self._cell_text(self.proxy_table, row, 1),
                        port=self._platform_field_text(instance_id, "proxy_port") or self._cell_text(self.proxy_table, row, 2),
                        username=self._platform_field_text(instance_id, "proxy_username") or self._cell_text(self.proxy_table, row, 3),
                        password=self._platform_field_text(instance_id, "proxy_password") or self._cell_text(self.proxy_table, row, 4),
                    ),
                    account=AccountConfig(
                        username=self._platform_field_text(instance_id, "account_username") or self._cell_text(self.account_table, row, 1),
                        password=self._platform_field_text(instance_id, "account_password") or self._cell_text(self.account_table, row, 2),
                        cookie_or_token=self._cell_text(self.account_table, row, 3),
                        note=self._cell_text(self.account_table, row, 4),
                    ),
                )
            )
        return configs

    def import_account_csv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "导入测试账号表格", "", "逗号分隔文件 (*.csv)")
        if not path:
            return
        with open(path, newline="", encoding="utf-8-sig") as handle:
            rows = list(csv.reader(handle))
        for row_index, row in enumerate(rows[: len(INSTANCE_IDS)]):
            for col_index, value in enumerate(row[:4], start=1):
                widget = self.account_table.cellWidget(row_index, col_index)
                if isinstance(widget, QLineEdit):
                    widget.setText(value)
            if row_index < len(INSTANCE_IDS):
                self._update_platform_status(INSTANCE_IDS[row_index])
        self.account_snapshot_changed.emit(self.account_snapshot())

    @staticmethod
    def _readonly_item(text: str) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        return item

    @staticmethod
    def _cell_text(table: QTableWidget, row: int, col: int) -> str:
        widget = table.cellWidget(row, col)
        if isinstance(widget, QLineEdit):
            return widget.text().strip()
        item = table.item(row, col)
        return item.text().strip() if item else ""


class CommandTerminal(QFrame):
    fire_requested = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("commandTerminal")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(8)

        header = QHBoxLayout()
        title = QLabel("影子指令终端与安全拦截器")
        title.setObjectName("panelTitle")
        self.fire_button = QPushButton("应急单步仿真释放")
        self.fire_button.setObjectName("simulation_fire_button")
        self.fire_button.clicked.connect(self.fire_requested.emit)
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.fire_button)
        layout.addLayout(header)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("command_terminal_tabs")

        self.terminal = QTextEdit()
        self.terminal.setObjectName("shadow_instruction_terminal")
        self.terminal.setReadOnly(True)
        self.terminal.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.terminal.setFont(QFont("Consolas", 10))
        self.terminal.document().setMaximumBlockCount(1200)
        JsonHighlighter(self.terminal.document())

        self.ws_raw_terminal = QTextEdit()
        self.ws_raw_terminal.setObjectName("ws_raw_traffic_terminal")
        self.ws_raw_terminal.setReadOnly(True)
        self.ws_raw_terminal.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.ws_raw_terminal.setFont(QFont("Consolas", 10))
        self.ws_raw_terminal.document().setMaximumBlockCount(2000)
        self.ws_raw_terminal.setPlaceholderText("等待 WebSocket 原始通信嗅探数据...")

        self.tabs.addTab(self.terminal, "影子指令终端")
        self.tabs.addTab(self.ws_raw_terminal, "WS 原始通信嗅探")
        layout.addWidget(self.tabs, 1)

    def render_manifest(self, manifest_json: str) -> None:
        self.terminal.setPlainText(manifest_json)
        self.terminal.verticalScrollBar().setValue(self.terminal.verticalScrollBar().maximum())

    def append_log(self, line: str) -> None:
        current = self.terminal.toPlainText()
        log_block = f"\n\n{line}"
        self.terminal.setPlainText(current + log_block if current else line)
        self.terminal.verticalScrollBar().setValue(self.terminal.verticalScrollBar().maximum())

    def start_room_entry_context(self, room_index: int, instance_ids: list[str] | None = None) -> None:
        targets = ", ".join(instance_ids or []) or "已选择平台"
        self.terminal.setPlainText(
            f"[进入房间] 准备进入房间 {room_index}；当前终端已切换到本次进房日志。"
            f"\n目标平台：{targets}"
            "\n历史完整影子日志仍保存在 live_logs 目录。"
        )
        self.terminal.verticalScrollBar().setValue(self.terminal.verticalScrollBar().maximum())

    def append_ws_raw(self, payload: object) -> None:
        event = payload if isinstance(payload, dict) else {"preview": str(payload)}
        suffix = " ..." if event.get("truncated") else ""
        line = (
            f"{event.get('timestamp_ms', '')} [{event.get('instance_id', '')}] "
            f"{event.get('direction', '')} {event.get('payload_kind', '')} "
            f"size={event.get('payload_size', '')} hash={event.get('payload_hash', '')} "
            f"{event.get('preview', '')}{suffix}"
        )
        self.ws_raw_terminal.append(line)
        self.ws_raw_terminal.verticalScrollBar().setValue(self.ws_raw_terminal.verticalScrollBar().maximum())


class MainDashboard(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("自动下注桌面控制台 - 影子审计模式")
        self.resize(1680, 980)
        self.current_manifest: ShadowTransactionManifest | None = None
        self.fire_worker: SimulationFireWorker | None = None
        self.latest_snapshots: dict[str, BrowserSnapshot] = {}
        self.latest_temporal_states: dict[str, TemporalStateSnapshot] = {}
        self.latest_shadow_temporal_states: dict[str, TemporalStateSnapshot] = {}
        self._room_entry_started_ms: dict[str, int] = {}
        self._room_entry_targets: dict[str, int] = {}
        self._room_entry_progress_log_keys: dict[str, str] = {}
        self.runtime_bet_ledger = RuntimeBetLedger()

        root = QWidget()
        self.setCentralWidget(root)
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(12, 12, 12, 12)
        root_layout.setSpacing(12)

        self.controller_panel = GlobalControllerPanel()
        root_layout.addWidget(self.controller_panel)

        monitor = QWidget()
        monitor_layout = QVBoxLayout(monitor)
        monitor_layout.setContentsMargins(0, 0, 0, 0)
        monitor_layout.setSpacing(12)

        self.global_panel = GlobalAuditorPanel()
        monitor_layout.addWidget(self.global_panel)

        self.instance_cards = {instance_id: InstanceCard(instance_id) for instance_id in INSTANCE_IDS}
        matrix = QGridLayout()
        matrix.setSpacing(12)
        matrix.addWidget(self.instance_cards["a1"], 0, 0)
        matrix.addWidget(self.instance_cards["a2"], 0, 1)
        matrix.addWidget(self.instance_cards["a3"], 1, 0)
        matrix.addWidget(self.instance_cards["a4"], 1, 1)
        monitor_layout.addLayout(matrix, 2)

        self.terminal = CommandTerminal()
        self.terminal.fire_requested.connect(self.on_fire_requested)
        monitor_layout.addWidget(self.terminal, 3)
        root_layout.addWidget(monitor, 1)

        self._apply_styles()
        self.probe_worker = DashboardProbeWorker()
        self.browser_worker = ClusterRuntimeWorker()
        self._hedge_orchestrator = self._build_hedge_orchestrator("a1")
        self.controller_panel.amount_changed.connect(self.on_amount_changed)
        self.controller_panel.hedge_model_changed.connect(self.probe_worker.set_hedge_model)
        self.controller_panel.proxy_snapshot_changed.connect(self.probe_worker.update_proxy_snapshot)
        self.controller_panel.account_snapshot_changed.connect(self.probe_worker.update_account_snapshot)
        self.controller_panel.regenerate_fingerprint_requested.connect(self.probe_worker.regenerate_fingerprint)
        self.controller_panel.reset_route_requested.connect(self.probe_worker.reset_route_contexts)
        self.controller_panel.launch_browser_requested.connect(self.on_launch_browsers_requested)
        self.controller_panel.restart_platform_requested.connect(
            lambda payload: self.browser_worker.restart_platform(payload if isinstance(payload, list) else [])
        )
        self.controller_panel.login_requested.connect(self.on_login_test_requested)
        self.controller_panel.fill_current_login_requested.connect(self.on_fill_current_login_requested)
        self.controller_panel.open_target_requested.connect(
            lambda: self.browser_worker.open_target_pages(self.controller_panel.browser_configs())
        )
        self.controller_panel.handoff_headless_requested.connect(
            lambda payload: self.browser_worker.handoff_to_headless(payload if isinstance(payload, list) else None)
        )
        self.controller_panel.enter_room_requested.connect(self.on_enter_room_requested)
        self.controller_panel.refresh_headless_requested.connect(
            lambda payload: self.browser_worker.refresh_headless_status(payload if isinstance(payload, list) else None)
        )
        self.controller_panel.release_headless_requested.connect(
            lambda payload: self.browser_worker.release_headless(payload if isinstance(payload, list) else None)
        )
        self.controller_panel.stop_browser_requested.connect(self.browser_worker.stop_browsers)
        self.controller_panel.start_execution_requested.connect(self.on_start_execution_requested)
        self.controller_panel.pause_execution_requested.connect(self.on_pause_execution_requested)
        self.controller_panel.stop_execution_requested.connect(self.on_stop_execution_requested)
        self.probe_worker.telemetry_ready.connect(self.on_telemetry_ready)
        self.probe_worker.manifest_ready.connect(self.on_manifest_ready)
        self.probe_worker.hedge_delta_ready.connect(self.global_panel.update_delta)
        self.probe_worker.log_ready.connect(self.terminal.append_log)
        self.browser_worker.state_ready.connect(self.on_temporal_state)
        self.browser_worker.health_ready.connect(self.on_cluster_health)
        self.browser_worker.ws_raw_ready.connect(self.terminal.append_ws_raw)
        self.browser_worker.audit_ready.connect(self.on_cluster_audit)
        self.browser_worker.runtime_v2_shadow_ready.connect(self.on_runtime_v2_shadow)
        self.browser_worker.log_ready.connect(self.terminal.append_log)
        self.probe_worker.start()
        self.browser_worker.start()

    def closeEvent(self, event) -> None:
        self.probe_worker.stop()
        self.browser_worker.stop()
        self.probe_worker.wait(1500)
        self.browser_worker.wait(2500)
        if self.fire_worker is not None and self.fire_worker.isRunning():
            self.fire_worker.wait(1500)
        super().closeEvent(event)

    def on_telemetry_ready(self, payload: object) -> None:
        telemetry = payload if isinstance(payload, dict) else {}
        for instance_id, item in telemetry.items():
            if instance_id in self.instance_cards and isinstance(item, InstanceTelemetry):
                self.instance_cards[instance_id].update_telemetry(item)

    def on_manifest_ready(self, manifest: object, manifest_json: str) -> None:
        if isinstance(manifest, ShadowTransactionManifest):
            self.current_manifest = manifest
        if isinstance(manifest, ShadowTransactionManifest):
            self.terminal.render_manifest(manifest_to_chinese_json(manifest))
        else:
            self.terminal.render_manifest(manifest_json)

    def on_amount_changed(self, amount: int) -> None:
        try:
            chips = decompose_value(int(amount), DENOMINATIONS, max_steps=4)
        except AutomationException as exc:
            self.terminal.append_log(f"[算法预览] 金额 {amount} 无法拆分：{exc.message}")
            return
        self.controller_panel.update_decomposition_preview(chips)
        self.probe_worker.set_base_amount(amount)

    def on_enter_room_requested(self, room_index: int, payload: object) -> None:
        configs = payload if isinstance(payload, list) else None
        instance_ids = [
            str(item.instance_id)
            for item in configs or []
            if isinstance(item, BrowserInstanceConfig)
        ]
        started_ms = now_ms()
        for instance_id in instance_ids:
            self._room_entry_started_ms[instance_id] = started_ms
            self._room_entry_targets[instance_id] = int(room_index)
            self._room_entry_progress_log_keys.pop(instance_id, None)
        if instance_ids:
            self.controller_panel.update_headless_progress(
                {instance_id: f"进入房间 {int(room_index)} 1/6 已发起，已用 0ms" for instance_id in instance_ids}
            )
        self.terminal.start_room_entry_context(int(room_index), instance_ids)
        self.browser_worker.enter_room(int(room_index), configs)

    def _on_hedge_execution_event(self, event: ExecutionEvent) -> None:
        payload = event.as_dict() if hasattr(event, "as_dict") else {
            "event_type": str(event),
            "state": "",
            "message": "",
            "safe_summary": {},
        }
        if isinstance(event, ExecutionEvent):
            self.controller_panel._append_algorithm_placeholder("执行编排器", self.controller_panel.format_hedge_execution_event(event))
        else:
            self.controller_panel._append_algorithm_placeholder("执行编排器", "收到编排器事件")
        self.terminal.append_log(f"[执行编排器] {json.dumps(payload, ensure_ascii=False, sort_keys=True)}")

    def _build_hedge_orchestrator(self, main_account_id: str) -> HedgeExecutionOrchestrator:
        return HedgeExecutionOrchestrator(
            event_listener=self._on_hedge_execution_event,
            shadow_executor=self._build_ui_shadow_executor(main_account_id),
            shadow_logger=self._append_shadow_execution_log,
        )

    def _build_ui_shadow_executor(self, main_account_id: str) -> LiveEnvironmentExecutor:
        instances: list[CanvasActionInstance] = []
        for instance_id in INSTANCE_IDS:
            page = _NoClickPage()
            instances.append(
                CanvasActionInstance(
                    account_id=instance_id,
                    role="main" if instance_id == main_account_id else "sub",
                    page=page,
                    adapter=CanvasBetAdapter(page, account_id=instance_id),
                    state_assertor=PageStateAssertor(detector=_StaticSafeDetector("-")),
                )
            )
        return LiveEnvironmentExecutor(
            instances,
            config=LiveExecutorConfig(),
            jitter_provider=lambda _account_id: 0,
        )

    def _append_shadow_execution_log(self, message: str) -> None:
        self.terminal.append_log(message)
        self.controller_panel._append_algorithm_placeholder("影子干跑", message)

    def on_start_execution_requested(self) -> None:
        try:
            config = self.controller_panel.execution_config()
        except Exception as exc:
            self.controller_panel._append_algorithm_placeholder("启动执行", f"执行配置无效：{exc}")
            self.terminal.append_log(f"[执行控制] 启动执行配置错误：{exc}")
            return
        self.controller_panel._append_algorithm_placeholder(
            "启动执行",
            "已提交影子执行计划入口，未接入真实下注点击。",
        )
        self.terminal.append_log(
            f"[执行控制] 已启动影子执行，主号={config.main_account_id}，剔除账号={len(config.excluded_account_ids)}。"
        )
        self._hedge_orchestrator = self._build_hedge_orchestrator(config.main_account_id)
        self._hedge_orchestrator.start(config)
        for instance_id in INSTANCE_IDS:
            snapshot = self.latest_shadow_temporal_states.get(instance_id) or self.latest_temporal_states.get(instance_id)
            if snapshot is not None:
                self._hedge_orchestrator.on_temporal_state(snapshot)

    def on_pause_execution_requested(self) -> None:
        self.controller_panel._append_algorithm_placeholder("暂停", "请求暂停编排器。")
        self.terminal.append_log("[执行控制] 暂停请求已记录。")
        self._hedge_orchestrator.pause()

    def on_stop_execution_requested(self) -> None:
        self.controller_panel._append_algorithm_placeholder("停止", "请求停止编排器。")
        self.terminal.append_log("[执行控制] 停止请求已记录。")
        self._hedge_orchestrator.stop()

    def on_launch_browsers_requested(self, payload: object) -> None:
        configs = payload if isinstance(payload, list) else []
        missing_login = [item.instance_id for item in configs if isinstance(item, BrowserInstanceConfig) and not item.login_url]
        if missing_login:
            QMessageBox.warning(self, "登录页缺失", "请先填写登录页地址，再启动 4 路浏览器。")
            return
        self.browser_worker.launch_browsers(configs)

    def on_login_test_requested(self, payload: object) -> None:
        configs = payload if isinstance(payload, list) else []
        if not configs:
            QMessageBox.warning(self, "配置缺失", "请先填写登录配置。")
            return
        if any(isinstance(item, BrowserInstanceConfig) and not item.login_url for item in configs):
            QMessageBox.warning(self, "登录页缺失", "请先填写登录页地址。")
            return
        runnable_configs = [
            item
            for item in configs
            if isinstance(item, BrowserInstanceConfig) and item.account.username and item.account.password
        ]
        if not runnable_configs:
            QMessageBox.warning(
                self,
                "账号资料缺失",
                "请至少填写 1 个实例的登录账号和密码。",
            )
            return
        self.terminal.append_log("[登录流程] 已启动独立浏览器，请人工完成登录、验证码和进入游戏页面；状态识别由多进程 WS 管线接管")
        self.browser_worker.fill_login_forms_with_configs(runnable_configs)

    def on_fill_current_login_requested(self, payload: object) -> None:
        configs = payload if isinstance(payload, list) else []
        runnable_configs = [
            item
            for item in configs
            if isinstance(item, BrowserInstanceConfig) and item.account.username and item.account.password
        ]
        if not runnable_configs:
            QMessageBox.warning(
                self,
                "账号资料缺失",
                "请至少填写 1 个实例的登录账号和密码。",
            )
            return
        self.terminal.append_log("[登录流程] 将在当前已打开的浏览器登录页填写账号密码，不重新打开浏览器")
        self.browser_worker.fill_current_login_forms_with_configs(runnable_configs)

    def on_browser_snapshot(self, payload: object) -> None:
        if not isinstance(payload, BrowserSnapshot):
            return
        card = self.instance_cards.get(payload.instance_id)
        if card is not None:
            card.update_browser_snapshot(payload)
        self.latest_snapshots[payload.instance_id] = payload
        self.global_panel.update_runtime_summary(self.latest_snapshots)

    def on_cluster_audit(self, instance_id: str, payload: object) -> None:
        data = payload if isinstance(payload, dict) else {}
        if data.get("audit_type") != "bet_confirmation":
            return
        event = self.runtime_bet_ledger.record_confirmation(str(instance_id), data)
        self._handle_bet_ledger_events([event])
        self.controller_panel.update_runtime_ledger_profit_loss(self.runtime_bet_ledger.account_profit_loss_yuan())
        latest = self.latest_temporal_states.get(str(instance_id))
        if latest is not None:
            self.controller_panel.update_platform_profit_loss(latest)

    def _handle_bet_ledger_events(self, events: list[dict[str, Any]]) -> None:
        for event in events:
            if not isinstance(event, dict):
                continue
            if event.get("event_type") == "duplicate_confirmation":
                continue
            message = str(event.get("message") or "")
            if not message:
                continue
            self.terminal.append_log(f"[下注账本] {message}")
            self.controller_panel._append_algorithm_placeholder("下注账本", message)

    def on_temporal_state(self, payload: object) -> None:
        snapshot = payload
        if isinstance(payload, dict):
            snapshot = TemporalStateSnapshot.from_mapping(payload)
        if not isinstance(snapshot, TemporalStateSnapshot):
            return
        card = self.instance_cards.get(snapshot.instance_id)
        if card is not None:
            card.update_temporal_state(snapshot)
        ledger_events = self.runtime_bet_ledger.update_balance_from_snapshot(snapshot)
        self._handle_bet_ledger_events(ledger_events)
        self.controller_panel.update_runtime_ledger_profit_loss(self.runtime_bet_ledger.account_profit_loss_yuan())
        self.controller_panel.update_platform_profit_loss(snapshot)
        self.latest_temporal_states[snapshot.instance_id] = snapshot
        self.global_panel.update_temporal_summary(self.latest_temporal_states)
        self._update_room_entry_progress_from_temporal_state(snapshot)
        self._hedge_orchestrator.on_temporal_state(snapshot)

    def on_runtime_v2_shadow(self, instance_id: str, payload: object) -> None:
        data = payload if isinstance(payload, dict) else {}
        instance_cards = self.__dict__.get("instance_cards")
        card = instance_cards.get(str(instance_id)) if isinstance(instance_cards, dict) else None
        if card is not None:
            card.update_runtime_v2_shadow(data)
        shadow_snapshot = self._shadow_temporal_snapshot_from_runtime_v2(str(instance_id), data)
        if shadow_snapshot is not None:
            shadow_states = self.__dict__.setdefault("latest_shadow_temporal_states", {})
            if isinstance(shadow_states, dict):
                shadow_states[str(instance_id)] = shadow_snapshot
            orchestrator = self.__dict__.get("_hedge_orchestrator")
            if orchestrator is not None:
                orchestrator.on_temporal_state(shadow_snapshot)
        room_index = self._room_entry_targets.get(str(instance_id))
        if room_index is None:
            current_status = self.controller_panel._headless_status.get(str(instance_id), "")
            room_index = self._room_index_from_progress_status(current_status)
        if room_index is None:
            return
        accepted_state = data.get("accepted_state") if isinstance(data.get("accepted_state"), dict) else {}
        stable_state = data.get("stable_state") if isinstance(data.get("stable_state"), dict) else {}
        accepted_balance = data.get("accepted_balance") if isinstance(data.get("accepted_balance"), dict) else {}
        progress_state = stable_state or accepted_state
        progress = self._room_entry_progress_from_shadow(
            str(instance_id),
            progress_state,
            accepted_balance,
            room_index,
            requires_stable=bool(stable_state),
        )
        if progress is None:
            return
        done_count, waiting_items, complete = progress
        elapsed_ms = self._room_entry_elapsed_ms(str(instance_id))
        if complete:
            status = f"房间 {room_index} 状态完整 6/6，用时 {elapsed_ms}ms"
            self._room_entry_targets.pop(str(instance_id), None)
            self._room_entry_started_ms.pop(str(instance_id), None)
        else:
            status = (
                f"房间 {room_index} 已确认 {done_count}/6，"
                f"等待{'、'.join(waiting_items)}，已用 {elapsed_ms}ms"
            )
        self.controller_panel.update_headless_progress({str(instance_id): status})
        self._append_room_entry_progress_log(str(instance_id), room_index, done_count, waiting_items, complete, elapsed_ms)

    def _shadow_temporal_snapshot_from_runtime_v2(
        self,
        instance_id: str,
        data: dict[str, Any],
    ) -> TemporalStateSnapshot | None:
        stable_state = data.get("stable_state") if isinstance(data.get("stable_state"), dict) else {}
        if not stable_state:
            return None
        accepted_balance = data.get("accepted_balance") if isinstance(data.get("accepted_balance"), dict) else {}
        session = data.get("session") if isinstance(data.get("session"), dict) else {}
        batch_id = str(stable_state.get("batch_id") or "").strip()
        countdown = InstanceCard._safe_int(stable_state.get("countdown"))
        if not batch_id or countdown is None or countdown < 0:
            return None

        latest = self.latest_temporal_states.get(instance_id)
        base_summary = dict(latest.safe_summary) if isinstance(latest, TemporalStateSnapshot) and isinstance(latest.safe_summary, dict) else {}
        room_label = InstanceCard._valid_room_label(
            stable_state.get("room_label"),
            session.get("expected_room_label"),
            session.get("active_room_label"),
            base_summary.get("locked_room_label"),
            base_summary.get("room_label"),
            base_summary.get("runtime_room_label"),
            base_summary.get("frontend_room_label"),
        )
        room_id = str(
            stable_state.get("room_id")
            or session.get("expected_room_id")
            or session.get("active_room_id")
            or base_summary.get("locked_room_id")
            or base_summary.get("room_id")
            or ""
        ).strip()
        limit_label = InstanceCard._valid_limit_label(
            stable_state.get("limit_label"),
            stable_state.get("runtime_limit_label"),
            stable_state.get("frontend_limit_label"),
            stable_state.get("canvas_limit_label"),
            stable_state.get("label_limit_label"),
            base_summary.get("limit_label"),
            base_summary.get("runtime_limit_label"),
            base_summary.get("frontend_limit_label"),
            base_summary.get("canvas_limit_label"),
            base_summary.get("label_limit_label"),
            base_summary.get("table_limit_label"),
            base_summary.get("runtime_table_limit"),
        )
        phase_key = str(stable_state.get("phase_key") or "").strip()
        timestamp_ms = int(data.get("timestamp_ms") or stable_state.get("timestamp_ms") or now_ms())
        try:
            ws_age = int(base_summary.get("last_ws_age_ms", -1))
        except (TypeError, ValueError):
            ws_age = -1
        ws_connected = bool(
            (isinstance(latest, TemporalStateSnapshot) and latest.ws_connected)
            or 0 <= ws_age <= 3000
            or stable_state
        )
        page_alive = True

        summary = dict(base_summary)
        shadow_summary = {
            "room_id": room_id,
            "room_label": room_label,
            "locked_room_id": room_id,
            "locked_room_label": room_label,
            "runtime_room_id": room_id,
            "runtime_room_label": room_label,
            "phase_key": phase_key,
            "phase_text": phase_key,
            "round_id": batch_id,
            "runtime_v2_shadow_bridge": True,
            "runtime_v2_shadow_online": True,
            "runtime_v2_shadow_source": stable_state.get("source") or "",
            "last_ws_age_ms": ws_age,
        }
        if limit_label:
            shadow_summary["limit_label"] = limit_label
            shadow_summary["runtime_limit_label"] = limit_label
        summary.update(shadow_summary)
        balance_text = str(
            accepted_balance.get("balance_text")
            or stable_state.get("balance_text")
            or (latest.ocr_balance if isinstance(latest, TemporalStateSnapshot) else "")
            or ""
        ).strip()
        return TemporalStateSnapshot(
            instance_id=instance_id,
            batch_id=batch_id,
            exact_countdown=countdown,
            ocr_balance=balance_text,
            timestamp_captured_ms=timestamp_ms,
            source="runtime_v2_shadow_stable",
            ws_connected=ws_connected,
            page_alive=page_alive,
            frame_id=int(latest.frame_id) if isinstance(latest, TemporalStateSnapshot) else 0,
            confidence=float(stable_state.get("confidence") or 0.0),
            safe_summary=summary,
        )

    def _update_room_entry_progress_from_temporal_state(self, snapshot: TemporalStateSnapshot) -> None:
        instance_id = str(snapshot.instance_id)
        current_status = self.controller_panel._headless_status.get(instance_id, "")
        room_index = self._room_entry_targets.get(instance_id) or self._room_index_from_progress_status(current_status)
        if room_index is None:
            return
        progress = self._room_entry_progress_from_snapshot(snapshot, room_index)
        if progress is None:
            return
        done_count, waiting_items, complete = progress
        elapsed_ms = self._room_entry_elapsed_ms(instance_id)
        if complete:
            status = f"房间 {room_index} 状态完整 6/6，用时 {elapsed_ms}ms"
            self._room_entry_targets.pop(instance_id, None)
            self._room_entry_started_ms.pop(instance_id, None)
        else:
            waiting_text = "、".join(waiting_items)
            status = f"房间 {room_index} 已确认 {done_count}/6，等待{waiting_text}，已用 {elapsed_ms}ms"
        self.controller_panel.update_headless_progress({instance_id: status})
        self._append_room_entry_progress_log(instance_id, room_index, done_count, waiting_items, complete, elapsed_ms)

    def _room_entry_elapsed_ms(self, instance_id: str) -> int:
        started_ms = int(self._room_entry_started_ms.get(instance_id) or now_ms())
        return max(0, now_ms() - started_ms)

    def _append_room_entry_progress_log(
        self,
        instance_id: str,
        room_index: int,
        done_count: int,
        waiting_items: list[str],
        complete: bool,
        elapsed_ms: int,
    ) -> None:
        key = f"{room_index}:{done_count}:{','.join(waiting_items)}:{complete}"
        if self._room_entry_progress_log_keys.get(instance_id) == key:
            return
        self._room_entry_progress_log_keys[instance_id] = key
        terminal = getattr(self, "__dict__", {}).get("terminal")
        if terminal is None:
            return
        if complete:
            terminal.append_log(f"[进入房间] {instance_id}: 房间 {room_index} 状态完整 6/6，用时 {elapsed_ms}ms")
        else:
            terminal.append_log(
                f"[进入房间] {instance_id}: 房间 {room_index} 已确认 {done_count}/6，"
                f"等待{'、'.join(waiting_items)}，已用 {elapsed_ms}ms"
            )

    @staticmethod
    def _room_entry_progress_from_snapshot(
        snapshot: TemporalStateSnapshot,
        room_index: int,
    ) -> tuple[int, list[str], bool] | None:
        summary = snapshot.safe_summary if isinstance(snapshot.safe_summary, dict) else {}
        expected_label = f"T{room_index:03d}"
        room_label = InstanceCard._valid_room_label(
            summary.get("locked_room_label"),
            summary.get("room_label"),
            summary.get("frontend_room_label"),
            summary.get("canvas_room_label"),
            summary.get("runtime_room_label"),
            summary.get("label_room_label"),
        )
        if not room_label:
            room_label = InstanceCard._room_label_from_room_id(
                summary.get("locked_room_id")
                or summary.get("room_id")
                or summary.get("frontend_room_id")
                or summary.get("runtime_room_id")
            )
        if room_label != expected_label:
            return None

        phase_values = (
            summary.get("frontend_phase_text"),
            summary.get("canvas_phase_text"),
            summary.get("label_phase_text"),
            summary.get("phase_text"),
            summary.get("frontend_runtime_action"),
            summary.get("runtime_action"),
            summary.get("ws_runtime_action"),
        )
        has_phase = any(str(item or "").strip() not in {"", "-", "unknown", "waiting for game state"} for item in phase_values)
        balance_text = str(snapshot.ocr_balance or "").strip()
        has_balance = bool(balance_text and balance_text not in {"-", "等待余额", "未识别"})
        has_batch = bool(str(snapshot.batch_id or "").strip() not in {"", "-"})
        has_countdown = snapshot.exact_countdown >= 0
        try:
            ws_age = int(summary.get("last_ws_age_ms", -1))
        except (TypeError, ValueError):
            ws_age = -1
        has_ws = bool(0 <= ws_age <= 300)

        checks = [
            ("房间号", True),
            ("余额", has_balance),
            ("局号", has_batch),
            ("倒计时", has_countdown),
            ("状态机", has_phase),
            ("WS", has_ws),
        ]
        waiting = [name for name, ok in checks if not ok]
        return len(checks) - len(waiting), waiting, not waiting

    def _room_entry_progress_from_shadow(
        self,
        instance_id: str,
        accepted_state: dict[str, Any],
        accepted_balance: dict[str, Any],
        room_index: int,
        *,
        requires_stable: bool = False,
    ) -> tuple[int, list[str], bool] | None:
        expected_label = f"T{room_index:03d}"
        room_label = InstanceCard._valid_room_label(accepted_state.get("room_label"))
        if room_label != expected_label:
            return None
        latest_states = getattr(self, "__dict__", {}).get("latest_temporal_states") or {}
        latest = latest_states.get(instance_id) if isinstance(latest_states, dict) else None
        summary = latest.safe_summary if isinstance(latest, TemporalStateSnapshot) and isinstance(latest.safe_summary, dict) else {}
        try:
            ws_age = int(summary.get("last_ws_age_ms", -1))
        except (TypeError, ValueError):
            ws_age = -1
        countdown = accepted_state.get("countdown")
        evidence = accepted_state.get("evidence") if isinstance(accepted_state.get("evidence"), dict) else {}
        stable_meta = evidence.get("stable_shadow") if isinstance(evidence, dict) else {}
        stable_gate = InstanceCard._shadow_stable_ready(stable_meta) or not requires_stable
        has_countdown = isinstance(countdown, int) and countdown >= 0 and stable_gate
        has_phase = str(accepted_state.get("phase_key") or "").strip() not in {"", "-", "unknown"} and stable_gate
        has_balance = str(accepted_balance.get("balance_text") or "").strip() not in {"", "-", "未识别"}
        has_batch = str(accepted_state.get("batch_id") or "").strip() not in {"", "-"} and stable_gate
        has_ws = bool(0 <= ws_age <= 300)
        checks = [
            ("房间号", True),
            ("余额", has_balance),
            ("局号", has_batch),
            ("倒计时", has_countdown),
            ("状态机", has_phase),
            ("WS", has_ws),
        ]
        waiting = [name for name, ok in checks if not ok]
        return len(checks) - len(waiting), waiting, not waiting

    @staticmethod
    def _room_index_from_progress_status(status: object) -> int | None:
        text = str(status or "")
        if not any(token in text for token in ("进入房间", "进房", "已点击房间")):
            return None
        match = re.search(r"(?:房间|房)\s*(\d+)", text)
        if not match:
            return None
        try:
            room_index = int(match.group(1))
        except (TypeError, ValueError):
            return None
        return room_index if 1 <= room_index <= 4 else None

    @staticmethod
    def _temporal_snapshot_confirms_room_entry(snapshot: TemporalStateSnapshot, room_index: int) -> bool:
        summary = snapshot.safe_summary if isinstance(snapshot.safe_summary, dict) else {}
        expected_label = f"T{room_index:03d}"
        room_label = InstanceCard._valid_room_label(
            summary.get("locked_room_label"),
            summary.get("room_label"),
            summary.get("frontend_room_label"),
            summary.get("canvas_room_label"),
            summary.get("runtime_room_label"),
            summary.get("label_room_label"),
        )
        if not room_label:
            room_label = InstanceCard._room_label_from_room_id(
                summary.get("locked_room_id")
                or summary.get("room_id")
                or summary.get("frontend_room_id")
                or summary.get("runtime_room_id")
            )
        if room_label != expected_label:
            return False
        phase_values = (
            summary.get("frontend_phase_text"),
            summary.get("canvas_phase_text"),
            summary.get("label_phase_text"),
            summary.get("phase_text"),
            summary.get("frontend_runtime_action"),
            summary.get("runtime_action"),
            summary.get("ws_runtime_action"),
        )
        has_phase = any(str(item or "").strip() not in {"", "unknown", "waiting for game state"} for item in phase_values)
        return bool(snapshot.batch_id or snapshot.exact_countdown >= 0 or has_phase)

    def on_cluster_health(self, instance_id: str, payload: object) -> None:
        card = self.instance_cards.get(str(instance_id))
        if card is None:
            return
        data = payload if isinstance(payload, dict) else {}
        card.update_cluster_health(data)
        status: str | None = None
        launch_state = str(data.get("game_launch_context") or "")
        if launch_state == "captured":
            status = "已捕获启动包"
        elif launch_state == "headless_ready":
            status = "无头大厅就绪"
        elif launch_state == "headless_released":
            status = "未接管"
        node_status = {
            "captured": "1/4 已抓真实URL",
            "headless_launching": "2/4 启动无头浏览器",
            "document_opened": "3/4 已打开真实URL",
            "loading_real_document": "3/4 等待游戏资源加载",
            "locating_hall_buttons": "3/4 检测4个入口按钮",
        }.get(launch_state)
        if node_status:
            status = node_status
        if launch_state == "headless_ready":
            button_count = data.get("hall_entry_button_count") or 4
            status = f"4/4 大厅就绪（入口{button_count}/4）"
        elif launch_state == "headless_status":
            ready = data.get("ready") if isinstance(data.get("ready"), dict) else {}
            button_count = int(data.get("hall_entry_button_count") or 0)
            if not data.get("headless_active"):
                status = "未接管"
            elif ready.get("game_ready"):
                status = "无头在线：已在房间"
            elif ready.get("hall_ready"):
                status = f"无头大厅就绪（入口{button_count}/4）"
            else:
                status = f"无头在线：未到大厅（入口{button_count}/4）"
        room_entry = str(data.get("room_entry") or "")
        if room_entry:
            room_index = data.get("room_index", "-")
            try:
                room_index_int = int(room_index)
            except (TypeError, ValueError):
                room_index_int = 0
            if 1 <= room_index_int <= 4:
                self._room_entry_targets[str(instance_id)] = room_index_int
                self._room_entry_started_ms.setdefault(str(instance_id), now_ms())
            elapsed_ms = self._room_entry_elapsed_ms(str(instance_id))
            if room_entry == "game_ready":
                status = f"进入房间 {room_index} 3/6 游戏页已打开，等待状态确认，已用 {elapsed_ms}ms"
            elif room_entry == "click_confirmed":
                status = f"进入房间 {room_index} 2/6 已点击入口，已用 {elapsed_ms}ms"
            elif room_entry == "entering":
                status = f"进入房间 {room_index} 1/6 进入中，已用 {elapsed_ms}ms"
            elif room_entry == "timeout":
                status = f"进房超时 {room_index}，已用 {elapsed_ms}ms"
            else:
                status = f"进房未确认 {room_index}，已用 {elapsed_ms}ms"
        if status:
            self.controller_panel.update_headless_progress({str(instance_id): status})


    def on_fire_requested(self) -> None:
        if self.current_manifest is None:
            QMessageBox.warning(self, "清单缺失", "当前还没有可审计的影子指令清单。")
            return
        if self.fire_worker is not None and self.fire_worker.isRunning():
            return
        self.fire_worker = SimulationFireWorker(self.current_manifest)
        self.fire_worker.blocked.connect(self.on_live_driver_blocked)
        self.fire_worker.failed.connect(self.on_fire_failed)
        self.fire_worker.log_ready.connect(self.terminal.append_log)
        self.fire_worker.start()

    def on_live_driver_blocked(self, payload: object) -> None:
        data = payload if isinstance(payload, dict) else {}
        x = data.get("target_x", "-")
        y = data.get("target_y", "-")
        trace_id = data.get("trace_id", "未知")
        action_text = {"select_chip": "选择筹码", "click_side": "点击区域"}.get(
            str(data.get("action", "")),
            str(data.get("action", "-")),
        )
        self.terminal.append_log(
            f"[故障即关闭] 追踪编号={trace_id} 拦截坐标=({x}, {y}) "
            f"实例={data.get('instance_id', '-')}"
        )
        QMessageBox.warning(
            self,
            "【故障即关闭】触发生产级物理熔断保护",
            "所有前置预检通过。已成功拦截前往真实页面的不可撤回点击。\n"
            f"追踪编号: {trace_id}\n"
            f"拦截坐标: ({x}, {y})\n"
            f"实例: {data.get('instance_id', '-')}\n"
            f"批次: {data.get('batch_id', '-')}\n"
            f"动作: {action_text} / {data.get('side', '-')}\n"
            f"金额: {data.get('expected_amount', '-')}",
        )

    def on_fire_failed(self, message: str) -> None:
        self.terminal.append_log(f"[拦截器异常] {message}")
        QMessageBox.critical(self, "安全拦截异常", message)

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget {
                background: #11161c;
                color: #e5eef7;
                font-family: "Microsoft YaHei", "Segoe UI", Arial;
            }
            QFrame#globalControllerPanel {
                background: #151b22;
                border: 1px solid #313946;
                border-radius: 8px;
            }
            QLabel#controllerTitle {
                color: #f8fafc;
                font-weight: 800;
                font-size: 17px;
                padding: 2px 0 4px 0;
            }
            QLabel#headlessProgressLabel {
                color: #9bdcff;
                background: #101820;
                border: 1px solid #263244;
                border-radius: 6px;
                padding: 6px 8px;
                font-weight: 700;
            }
            QFrame#controllerSection {
                background: #171c22;
                border: 1px solid #313946;
                border-radius: 8px;
            }
            QTabWidget#controllerModeTabs::pane {
                border: 1px solid #313946;
                border-radius: 8px;
                top: -1px;
                background: #111820;
            }
            QTabWidget#controllerModeTabs QTabBar::tab {
                background: #18202a;
                color: #94a3b8;
                border: 1px solid #313946;
                padding: 7px 14px;
                margin-right: 4px;
                border-top-left-radius: 5px;
                border-top-right-radius: 5px;
                font-weight: 700;
            }
            QTabWidget#controllerModeTabs QTabBar::tab:selected {
                background: #0f2a3a;
                color: #8bdcff;
                border-bottom-color: #0f2a3a;
            }
            QLabel#controllerSectionTitle {
                color: #bae6fd;
                font-weight: 700;
                font-size: 13px;
            }
            QLabel#decompositionPreview {
                background: #202733;
                color: #fde68a;
                border: 1px solid #343d4a;
                border-radius: 5px;
                padding: 7px 8px;
                font-weight: 700;
            }
            QTextEdit#algorithmExecutionLog {
                background: #0c1117;
                color: #dbeafe;
                border: 1px solid #313946;
                border-radius: 6px;
                padding: 8px;
                selection-background-color: #1d4ed8;
            }
            QLineEdit, QComboBox, QSpinBox {
                background: #202733;
                color: #e5eef7;
                border: 1px solid #343d4a;
                border-radius: 5px;
                padding: 5px 7px;
                min-height: 24px;
            }
            QLineEdit:focus, QComboBox:focus, QSpinBox:focus {
                border: 1px solid #38bdf8;
            }
            QTableWidget {
                background: #111820;
                color: #e5eef7;
                border: 1px solid #313946;
                border-radius: 6px;
                gridline-color: #2b3440;
            }
            QHeaderView::section {
                background: #202733;
                color: #bae6fd;
                border: 0;
                border-right: 1px solid #313946;
                padding: 5px;
                font-weight: 700;
            }
            QTableWidget::item {
                padding: 4px;
            }
            QPushButton {
                background: #202733;
                color: #e5eef7;
                border: 1px solid #3b4654;
                border-radius: 6px;
                padding: 7px 9px;
                font-weight: 700;
            }
            QPushButton:hover {
                background: #263241;
                border-color: #38bdf8;
            }
            QFrame#globalAuditorPanel,
            QFrame#commandTerminal,
            QFrame#a1_instance_card,
            QFrame#a2_instance_card,
            QFrame#a3_instance_card,
            QFrame#a4_instance_card {
                background: #171c22;
                border: 1px solid #313946;
                border-radius: 8px;
            }
            QLabel#panelTitle {
                color: #f8fafc;
                font-weight: 700;
                font-size: 15px;
            }
            QLabel#hedge_equation {
                color: #bae6fd;
                font-size: 15px;
                padding: 0 8px;
            }
            QLabel[tone="ok"] {
                background: #123226;
                color: #34d399;
                border: 1px solid #1f7a56;
                border-radius: 4px;
                padding: 4px 10px;
                font-weight: 700;
            }
            QLabel[tone="warning"] {
                background: #322814;
                color: #fbbf24;
                border: 1px solid #8a6a1d;
                border-radius: 4px;
                padding: 4px 10px;
                font-weight: 700;
            }
            QLabel[tone="danger"] {
                background: #3a181b;
                color: #f87171;
                border: 1px solid #8b2b31;
                border-radius: 4px;
                padding: 4px 10px;
                font-weight: 700;
            }
            QLabel#a1_role { color: #2dd4bf; }
            QLabel#a2_role, QLabel#a3_role, QLabel#a4_role { color: #fbbf24; }
            QLabel#metricName {
                color: #8fa3b5;
                font-size: 11px;
            }
            QFrame#metricBox {
                background: #202733;
                border: 1px solid #343d4a;
                border-radius: 6px;
            }
            QLabel#shadow_instruction_terminal {}
            QTextEdit#shadow_instruction_terminal {
                background: #0c1117;
                color: #cbd5e1;
                border: 1px solid #313946;
                border-radius: 6px;
                padding: 8px;
                selection-background-color: #1d4ed8;
            }
            QTextEdit#ws_raw_traffic_terminal {
                background: #090d13;
                color: #a7f3d0;
                border: 1px solid #1f3a34;
                border-radius: 6px;
                padding: 8px;
                selection-background-color: #047857;
            }
            QTabWidget#command_terminal_tabs::pane {
                border: 0;
                top: -1px;
            }
            QTabWidget#command_terminal_tabs QTabBar::tab {
                background: #18202a;
                color: #94a3b8;
                border: 1px solid #313946;
                padding: 6px 12px;
                margin-right: 4px;
                border-top-left-radius: 5px;
                border-top-right-radius: 5px;
            }
            QTabWidget#command_terminal_tabs QTabBar::tab:selected {
                background: #0c1117;
                color: #e2e8f0;
                border-bottom-color: #0c1117;
            }
            QPushButton#simulation_fire_button {
                background: #7f1d1d;
                color: #fee2e2;
                border: 1px solid #b91c1c;
                border-radius: 6px;
                padding: 9px 14px;
                font-weight: 700;
            }
            QPushButton#simulation_fire_button:hover {
                background: #991b1b;
            }
            """
        )


def main() -> int:
    app = QApplication(sys.argv)
    window = MainDashboard()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
