"""A lightweight phase-1/2/3 hedge UI dashboard."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path
from time import monotonic
from typing import Any

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QFont, QFontDatabase
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QAbstractSpinBox,
    QApplication,
    QButtonGroup,
    QComboBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from bet_desktop.ui.lightweight_browser_adapter import CommandBrowserControlAdapter, FakeBrowserControlAdapter
from bet_desktop.ui.lightweight_controller import LightweightController
from bet_desktop.ui.lightweight_models import ACCOUNT_IDS, DEFAULT_MAIN_ACCOUNT, parse_proxy_bundle_line

SIDE_TEXT_CN = {"banker": "庄", "player": "闲", "tie": "和"}


class LightweightDashboard(QMainWindow):
    def __init__(
        self,
        controller: LightweightController | None = None,
        use_command_adapter: bool = False,
    ) -> None:
        super().__init__()
        self.setWindowTitle("轻量对冲控制台")
        self.resize(1320, 860)
        self._ui_font_family = self._install_ui_font()

        if controller is None:
            adapter = CommandBrowserControlAdapter() if use_command_adapter else FakeBrowserControlAdapter()
            controller = LightweightController(adapter=adapter)
        self.controller = controller

        self._ui_logs: list[str] = []
        self.slot_cards: dict[str, dict[str, QWidget]] = {}
        self.summary_cards: dict[str, dict[str, QLabel]] = {}
        self.role_buttons: dict[str, QPushButton] = {}
        self.room_index_buttons: dict[int, list[QPushButton]] = {room_index: [] for room_index in (1, 2, 3)}
        self.account_cards: dict[str, dict[str, QWidget]] = {}
        self.plan_rows: dict[str, dict[str, QWidget]] = {}
        self.health_labels: dict[str, QLabel] = {}
        self.turnover_labels: dict[str, dict[str, QLabel]] = {}
        self.pnl_rows: dict[str, dict[str, QLabel]] = {}
        self.outcome_labels: dict[str, QLabel] = {}
        self.round_table = None
        self._current_balances: dict[str, Decimal] = {}
        self._initial_balances: dict[str, Decimal] = {}
        self._deposit_totals: dict[str, Decimal] = {}
        self._withdraw_totals: dict[str, Decimal] = {}
        self._profit_tracking_active = True
        self._turnover_reset_index = 0
        self._outcome_pending: list[dict[str, Any]] = []
        self._outcome_recorded_rounds: set[str] = set()
        self._outcome_counts: dict[str, int] = {"banker": 0, "player": 0, "tie": 0}
        self._outcome_commission_total = Decimal("0")
        self._account_round_ids: dict[str, str] = {}
        self._run_started_at: float | None = None
        self._plan_account_states: dict[str, str] = self.controller.plan_account_states

        root = QWidget()
        self.setCentralWidget(root)
        self._build_ui(root)
        self._apply_style()
        self._bind_controller()
        self._refresh_from_controller()
        self._runtime_poll_timer = QTimer(self)
        self._runtime_poll_timer.setInterval(1200)
        self._runtime_poll_timer.timeout.connect(self._poll_runtime_events)
        self._runtime_poll_timer.start()
        self._append_log("轻量窗口初始化完成")

    def closeEvent(self, event) -> None:
        if self._runtime_poll_timer.isActive():
            self._runtime_poll_timer.stop()
        self.controller.shutdown_runtime()
        super().closeEvent(event)

    def _build_ui(self, root: QWidget) -> None:
        layout = QVBoxLayout(root)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        layout.addWidget(self._build_topbar())

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self._scroll_page(self._build_config_page()), "平台配置")
        self.tabs.addTab(self._scroll_page(self._build_runtime_page()), "运行控制")
        self.tabs.addTab(self._scroll_page(self._build_records_page()), "执行记录")
        layout.addWidget(self.tabs, 1)

    def _build_topbar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("topbar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        mark = QLabel("轻")
        mark.setObjectName("brandMark")
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(mark)

        title_box = QVBoxLayout()
        title_box.setSpacing(1)
        title = QLabel("轻量对冲控制台")
        title.setObjectName("appTitle")
        self.main_summary = QLabel("当前主号 a2")
        self.main_summary.setObjectName("subline")
        title_box.addWidget(title)
        title_box.addWidget(self.main_summary)
        layout.addLayout(title_box)
        layout.addStretch()
        return bar

    def _build_config_page(self) -> QWidget:
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(2, 2, 2, 2)
        page_layout.setSpacing(12)

        summary_panel, summary_body = self._panel("平台摘要", "只显示关键运行信息")
        summary_grid = QGridLayout()
        summary_grid.setSpacing(10)
        for index, account_id in enumerate(ACCOUNT_IDS):
            card = self._build_platform_summary_card(account_id)
            summary_grid.addWidget(card, index // 2, index % 2)
        summary_body.addLayout(summary_grid)
        page_layout.addWidget(summary_panel)

        batch_strip = QFrame()
        batch_strip.setObjectName("batchStrip")
        batch_layout = QHBoxLayout(batch_strip)
        batch_layout.setContentsMargins(12, 10, 12, 10)
        hint = QLabel("批量动作放在 4 个摘要卡片下方，避免配置页顶部拥挤。")
        hint.setObjectName("hint")
        batch_layout.addWidget(hint, 1)
        batch_layout.addWidget(self._button("打开登录页", "", self._on_open_login_pages))
        batch_layout.addWidget(self._button("批量填登录", "", self._on_fill_login))
        batch_layout.addWidget(self._button("接管副号", "primary", self._on_handoff))
        batch_layout.addWidget(self._build_room_selector())
        batch_layout.addWidget(self._button("副号进房", "success", self._on_enter_room))
        batch_layout.addWidget(self._button("释放无头", "", self._on_release))
        batch_layout.addWidget(self._button("批量停止", "danger", self.controller.batch_stop_clicked))
        page_layout.addWidget(batch_strip)

        edit_panel, edit_body = self._panel("4 平台统一编辑", "保存")
        edit_actions = QHBoxLayout()
        edit_actions.addStretch()
        edit_actions.addWidget(self._button("测试代理", "", self._protected_notice, enabled=False))
        edit_actions.addWidget(self._button("保存 4 平台配置", "success", self._on_save_config))
        edit_body.addLayout(edit_actions)

        proxy_hint = QLabel("代理完整格式直接粘贴到每个账号行：IP|端口|代理账号|代理密码|到期时间，保存时自动拆分。")
        proxy_hint.setObjectName("hint")
        edit_body.addWidget(proxy_hint)

        edit_grid = QGridLayout()
        edit_grid.setHorizontalSpacing(8)
        edit_grid.setVerticalSpacing(8)
        headers = [
            "平台",
            "名称",
            "登录页",
            "平台账号",
            "密码",
            "代理完整格式",
        ]
        for col, text in enumerate(headers):
            label = QLabel(text)
            label.setObjectName("tableHeader")
            edit_grid.addWidget(label, 0, col)
        for row, account_id in enumerate(ACCOUNT_IDS, start=1):
            self._build_edit_row(edit_grid, row, account_id)
        edit_body.addLayout(edit_grid)
        page_layout.addWidget(edit_panel)
        page_layout.addStretch()
        return page

    def _build_runtime_page(self) -> QWidget:
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(12)

        left_col = QWidget()
        left_col.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        left_col.setMinimumWidth(380)
        left_col.setMaximumWidth(440)
        left = QVBoxLayout(left_col)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(12)
        left.addWidget(self._build_run_control_panel())
        left.addWidget(self._build_rounds_panel())
        left.addWidget(self._build_gate_panel())
        left.addWidget(self._build_advanced_panel())
        left.addStretch()
        layout.addWidget(left_col)

        center_col = QWidget()
        center_col.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        center_col.setMinimumWidth(470)
        center = QVBoxLayout(center_col)
        center.setContentsMargins(0, 0, 0, 0)
        center.setSpacing(12)
        center.addWidget(self._build_account_status_panel())
        center.addWidget(self._build_log_panel())
        center.addStretch()
        layout.addWidget(center_col, 1)

        right_col = QWidget()
        right_col.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        right_col.setMinimumWidth(300)
        right_col.setMaximumWidth(380)
        right = QVBoxLayout(right_col)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(12)
        right.addWidget(self._build_plan_panel())
        right.addWidget(self._build_health_panel())
        right.addWidget(self._build_outcome_panel())
        right.addStretch()
        layout.addWidget(right_col)
        return page

    def _build_records_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(2, 2, 2, 2)
        panel, body = self._panel("执行记录", "摘要")
        actions = QHBoxLayout()
        actions.addStretch()
        actions.addWidget(self._button("导出记录", "", self._protected_notice, enabled=False))
        actions.addWidget(self._button("清空记录", "", self._protected_notice, enabled=False))
        body.addLayout(actions)

        self.records_table = self._table(["轮次", "房间", "倒计时", "间隔", "耗时", "缺口", "状态"], 5)
        body.addWidget(self.records_table)
        layout.addWidget(panel)
        layout.addStretch()
        self._seed_records_table()
        return page

    def _build_run_control_panel(self) -> QWidget:
        panel, body = self._panel("运行控制", "待命")
        body.setSpacing(10)
        head = QHBoxLayout()
        head.addWidget(QLabel("当前主号"))
        head.addStretch()
        self.run_state_label = self._pill("待命", "good")
        head.addWidget(self.run_state_label)
        body.addLayout(head)

        role_row = QHBoxLayout()
        self.role_group = QButtonGroup(self)
        self.role_group.setExclusive(True)
        for account_id in ACCOUNT_IDS:
            btn = self._button(account_id, "segment", None)
            btn.setCheckable(True)
            btn.clicked.connect(lambda checked=False, value=account_id: self.controller.set_main_account(value))
            self.role_buttons[account_id] = btn
            self.role_group.addButton(btn)
            role_row.addWidget(btn)
        body.addLayout(role_row)

        note = QLabel("切换后，接管对象和本轮对冲计划自动按新的主号重排。")
        note.setWordWrap(True)
        note.setObjectName("hint")
        body.addWidget(note)
        body.addWidget(self._build_room_selector())
        room_action_row = QHBoxLayout()
        room_action_row.setSpacing(8)
        room_action_row.addWidget(self._button("副号进房", "", self._on_enter_room))
        room_action_row.addWidget(self._button("全部进房", "", self._on_enter_room_all))
        room_action_row.addWidget(self._button("释放", "", self._on_release))
        body.addLayout(room_action_row)
        body.addWidget(self._button("启动轻量控制", "success", self._on_start))
        body.addWidget(self._button("暂停", "warn", self.controller.pause_clicked))
        body.addWidget(self._button("急停", "danger", self.controller.stop_clicked))
        return panel

    def _build_advanced_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("panel")
        wrapper = QVBoxLayout(panel)
        wrapper.setContentsMargins(12, 12, 12, 12)
        wrapper.setSpacing(10)

        head = QHBoxLayout()
        title = QLabel("高级设置")
        title.setObjectName("panelTitle")
        head.addWidget(title)
        head.addStretch()
        head.addWidget(self._pill("调试", "info"))
        toggle_btn = self._button("展开", "", None)
        toggle_btn.setCheckable(True)
        head.addWidget(toggle_btn)
        wrapper.addLayout(head)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(10)

        content_layout.addWidget(self._advanced_title("手动动作", "人工"))
        control_grid = QGridLayout()
        control_grid.setSpacing(8)
        self.handoff_btn = self._button("a1/a3/a4 接管", "primary", self._on_handoff)
        control_grid.addWidget(self.handoff_btn, 0, 0, 1, 2)
        control_grid.addWidget(self._button("副号进房", "", self._on_enter_room), 1, 0)
        control_grid.addWidget(self._button("刷新无头", "", self._on_refresh_headless), 1, 1)
        control_grid.addWidget(self._button("测试发一轮", "success", self.controller.test_one_round_clicked), 2, 0, 1, 2)
        control_grid.addWidget(self._button("测试 10 轮", "", self.controller.test_ten_rounds_clicked), 3, 0, 1, 2)
        control_grid.addWidget(self._button("打开观察窗", "", self._protected_notice, enabled=False), 4, 0, 1, 2)
        content_layout.addLayout(control_grid)
        content_layout.addWidget(self._mode_banner())

        content_layout.addWidget(self._advanced_title("策略参数", "调参"))
        content_layout.addLayout(self._strategy_form())

        content_layout.addWidget(self._advanced_title("资源策略", "低耗"))
        for label, value in [
            ("平时刷新", "1s"),
            ("临近窗口", "200ms"),
            ("坐标采集", "异常触发"),
            ("日志刷新", "摘要"),
        ]:
            content_layout.addWidget(self._metric_row(label, value))

        wrapper.addWidget(content)

        def toggle(checked: bool) -> None:
            content.setVisible(checked)
            toggle_btn.setText("收起" if checked else "展开")

        toggle_btn.toggled.connect(toggle)
        toggle(False)
        return panel

    def _build_account_status_panel(self) -> QWidget:
        panel, body = self._panel("账号状态", "实时状态")
        panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        grid = QGridLayout()
        grid.setSpacing(8)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.setRowStretch(0, 1)
        grid.setRowStretch(1, 1)
        for index, account_id in enumerate(ACCOUNT_IDS):
            grid.addWidget(self._build_account_card(account_id), index // 2, index % 2)
        body.addLayout(grid)
        return panel

    def _build_gate_panel(self) -> QWidget:
        panel, body = self._panel("下注流水累计", "")
        panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        header = panel.layout().itemAt(0).layout()
        if header is not None:
            header.addWidget(self._button("重置流水", "", self._on_reset_turnover))
        turnover_grid = QGridLayout()
        turnover_grid.setSpacing(8)
        for index, account_id in enumerate(ACCOUNT_IDS):
            turnover_grid.addWidget(self._turnover_item(account_id), index // 2, index % 2)
        body.addLayout(turnover_grid)
        return panel

    def _build_rounds_panel(self) -> QWidget:
        panel, body = self._panel("执行轮次", "0/10")
        panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.round_table = self._table(
            ["轮次", "倒计时", "间隔", "a1", "a2", "a3", "a4", "耗时", "缺口"],
            3,
        )
        self.round_table.setMinimumHeight(108)
        self.round_table.setMaximumHeight(118)
        self.round_table.horizontalHeader().setMinimumSectionSize(24)
        self.round_table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body.addWidget(self.round_table)
        self._seed_round_table()
        return panel

    def _build_plan_panel(self) -> QWidget:
        panel, body = self._panel("本轮计划", "对冲")
        header = panel.layout().itemAt(0).layout()
        if header is not None:
            self.plan_round_label = self._pill("计划 0 轮", "info")
            header.addWidget(self.plan_round_label)
        for account_id in ACCOUNT_IDS:
            row = QFrame()
            row.setObjectName("planLine")
            row.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            layout = QVBoxLayout(row)
            layout.setContentsMargins(8, 6, 8, 6)
            layout.setSpacing(4)
            row.setMinimumHeight(68)
            amount = QLabel("-")
            amount.setObjectName("planAmount")
            amount.setAlignment(Qt.AlignmentFlag.AlignCenter)
            amount.setFixedWidth(68)
            side = QLabel(f"{account_id} · 等待")
            side.setObjectName("planTitle")
            side.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            chips = QLabel("未计算")
            chips.setObjectName("planMeta")
            chips.setWordWrap(True)
            chips.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            role = self._pill("副", "")
            state = self._pill("正常", "")
            action = self._button(
                "下局剔除",
                "",
                lambda checked=False, account_id=account_id: self._on_plan_state_action(account_id),
            )
            action.setMinimumWidth(72)
            top_line = QHBoxLayout()
            top_line.setSpacing(8)
            top_line.addWidget(amount)
            top_line.addWidget(side, 1)
            top_line.addWidget(role)
            detail_line = QHBoxLayout()
            detail_line.setSpacing(8)
            detail_line.addWidget(chips, 1)
            detail_line.addWidget(state)
            detail_line.addWidget(action)
            layout.addLayout(top_line)
            layout.addLayout(detail_line)
            body.addWidget(row)
            self.plan_rows[account_id] = {
                "row": row,
                "amount": amount,
                "side": side,
                "chips": chips,
                "role": role,
                "state": state,
                "action": action,
            }
        panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        body.addStretch()
        return panel

    def _build_health_panel(self) -> QWidget:
        panel, body = self._panel("运行摘要", "正常")
        panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        grid = QGridLayout()
        grid.setSpacing(8)
        for index, (key, value, label) in enumerate([
            ("rounds", "0", "测试轮次"),
            ("missing", "0", "总缺口"),
            ("max_round_ms", "-", "最慢整轮"),
            ("max_click_ms", "-", "最慢点击"),
            ("runtime", "00:00:00", "运行时间"),
        ]):
            grid.addWidget(self._health_box(value, label, key), index // 3, index % 3)
        body.addLayout(grid)
        return panel

    def _build_outcome_panel(self) -> QWidget:
        panel, body = self._panel("开奖统计", "启动后")
        panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self._outcome_metric_box("0.00", "抽成累计", "commission"), 1)
        counts = QVBoxLayout()
        counts.setSpacing(6)
        counts.addWidget(self._outcome_count_row("庄次数", "0", "banker"))
        counts.addWidget(self._outcome_count_row("闲次数", "0", "player"))
        counts.addWidget(self._outcome_count_row("和次数", "0", "tie"))
        row.addLayout(counts, 1)
        body.addLayout(row)
        return panel

    def _build_log_panel(self) -> QWidget:
        panel, body = self._panel("单账号盈亏", "启动后统计")
        panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        grid = QGridLayout()
        grid.setSpacing(8)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        for index, account_id in enumerate(ACCOUNT_IDS):
            item = self._pnl_item(account_id)
            self._enrich_pnl_item(item, account_id)
            grid.addWidget(item, index // 2, index % 2)
        body.addLayout(grid)
        return panel

    def _build_platform_summary_card(self, account_id: str) -> QWidget:
        card = QFrame()
        card.setObjectName("summaryCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(7)

        top = QHBoxLayout()
        badge = self._badge(account_id)
        top.addWidget(badge)
        name_box = QVBoxLayout()
        name_box.setSpacing(0)
        name = QLabel("未命名平台")
        name.setObjectName("cardTitle")
        role = QLabel("副号")
        role.setObjectName("hint")
        name_box.addWidget(name)
        name_box.addWidget(role)
        top.addLayout(name_box)
        top.addStretch()
        state = self._pill("可用", "good")
        top.addWidget(state)
        layout.addLayout(top)

        expire = QLabel("代理到期：-")
        account = QLabel("平台账号：-")
        layout.addWidget(expire)
        layout.addWidget(account)
        self.summary_cards[account_id] = {
            "name": name,
            "role": role,
            "state": state,
            "expire": expire,
            "account": account,
            "badge": badge,
        }
        return card

    def _build_edit_row(self, grid: QGridLayout, row: int, account_id: str) -> None:
        badge = self._badge(account_id)
        grid.addWidget(badge, row, 0)

        fields: dict[str, QLineEdit] = {}
        keys = [
            "display_name",
            "login_url",
            "account_username",
            "account_password",
            "proxy_bundle",
        ]
        field_widths = {
            "display_name": 120,
            "login_url": 150,
            "account_username": 140,
            "account_password": 120,
            "proxy_bundle": 390,
        }
        for offset, key in enumerate(keys, start=1):
            edit = QLineEdit()
            edit.setMinimumWidth(field_widths[key])
            edit.setMaximumWidth(field_widths[key])
            if key == "proxy_bundle":
                edit.setPlaceholderText("117.68.75.165|8578|revf18h1|IVSrG6hd|2026-07-18")
                edit.setToolTip("格式：IP|端口|代理账号|代理密码|到期时间。保存时自动拆分到后端配置。")
            if key == "account_password":
                edit.setEchoMode(QLineEdit.EchoMode.Password)
            fields[key] = edit
            grid.addWidget(edit, row, offset)

        self.slot_cards[account_id] = fields

    def _build_account_card(self, account_id: str) -> QWidget:
        card = QFrame()
        card.setObjectName("accountCard")
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        card.setMaximumHeight(190)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(9, 7, 9, 7)
        layout.setSpacing(5)

        top = QHBoxLayout()
        badge = self._badge(account_id)
        top.addWidget(badge)
        labels = QVBoxLayout()
        role = QLabel("副号 · 未启动")
        role.setObjectName("hint")
        state = self._pill("等待", "")
        labels.addWidget(QLabel(account_id))
        labels.addWidget(role)
        top.addLayout(labels, 1)
        top.addWidget(state)
        layout.addLayout(top)

        betting_zone = QLabel("未同步")
        betting_zone.setObjectName("countdown")
        betting_zone.setMinimumHeight(30)
        betting_zone.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout.addWidget(betting_zone)
        round_id = QLabel("局号：-")
        balance = QLabel("余额：-")
        room = QLabel("房间：-")
        target_room = QLabel("目标：-")
        room_progress = QLabel("进度：-")
        room_progress.setObjectName("hint")
        info_grid = QGridLayout()
        info_grid.setHorizontalSpacing(8)
        info_grid.setVerticalSpacing(3)
        info_grid.addWidget(room, 0, 0)
        info_grid.addWidget(balance, 0, 1)
        info_grid.addWidget(round_id, 1, 0, 1, 2)
        info_grid.addWidget(target_room, 2, 0)
        info_grid.addWidget(room_progress, 2, 1)
        info_grid.setColumnStretch(0, 1)
        info_grid.setColumnStretch(1, 1)
        layout.addLayout(info_grid)

        foot = QHBoxLayout()
        restart = self._button("重启", "", lambda checked=False, account_id=account_id: self._on_account_restart(account_id))
        handoff = self._button("接管", "", lambda checked=False, account_id=account_id: self._on_account_handoff(account_id))
        enter_room = self._button("进房", "", lambda checked=False, account_id=account_id: self._on_account_enter_room(account_id))
        foot.addWidget(restart)
        foot.addWidget(handoff)
        foot.addWidget(enter_room)
        layout.addLayout(foot)
        self.account_cards[account_id] = {
            "role": role,
            "state": state,
            "betting_zone": betting_zone,
            "room": room,
            "round": round_id,
            "balance": balance,
            "target_room": target_room,
            "room_progress": room_progress,
            "handoff": handoff,
        }
        return card

    def _strategy_form(self) -> QVBoxLayout:
        layout = QVBoxLayout()
        interval_row = QHBoxLayout()
        interval_row.addWidget(QLabel("点击间隔"))
        for value in (200, 250, 300):
            btn = self._button(f"{value}ms", "segment", None)
            btn.clicked.connect(lambda checked=False, ms=value: self.click_interval_input.setValue(ms))
            interval_row.addWidget(btn)
        layout.addLayout(interval_row)

        self.amount_min_input = self._spin(1, 9999, 80)
        self.amount_max_input = self._spin(1, 9999, 150)
        self.min_balance_input = self._spin(0, 99999, 0)
        self.main_successor_input = QComboBox()
        self.main_successor_input.addItem("余额最高自动继承", "")
        for account_id in ACCOUNT_IDS:
            self.main_successor_input.addItem(account_id, account_id)
        self.click_interval_input = self._spin(1, 3000, 200)
        self.min_countdown_input = self._spin(0, 300, 10)
        self.confirm_ms_input = self._spin(0, 5000, 1200)
        for widget in [
            self.amount_min_input,
            self.amount_max_input,
            self.min_balance_input,
            self.click_interval_input,
            self.min_countdown_input,
            self.confirm_ms_input,
        ]:
            widget.valueChanged.connect(self._on_strategy_preview_changed)
        self.main_successor_input.currentIndexChanged.connect(self._on_strategy_preview_changed)
        for label, widget, suffix in [
            ("主单金额下限", self.amount_min_input, "元"),
            ("主单金额上限", self.amount_max_input, "元"),
            ("点击间隔", self.click_interval_input, "ms"),
            ("最小发号倒计时", self.min_countdown_input, "秒"),
            ("确认等待", self.confirm_ms_input, "ms"),
        ]:
            row = QHBoxLayout()
            row.addWidget(QLabel(label))
            row.addWidget(widget, 1)
            row.addWidget(self._pill(suffix, ""))
            layout.addLayout(row)
        min_balance_row = QHBoxLayout()
        min_balance_row.addWidget(QLabel("低余额剔除线"))
        min_balance_row.addWidget(self.min_balance_input, 1)
        min_balance_row.addWidget(self._pill("元", ""))
        layout.addLayout(min_balance_row)
        successor_row = QHBoxLayout()
        successor_row.addWidget(QLabel("主号不足继承"))
        successor_row.addWidget(self.main_successor_input, 1)
        layout.addLayout(successor_row)
        layout.addWidget(self._button("应用高级参数", "primary", self._on_apply_advanced))
        return layout

    def _panel(self, title: str, pill_text: str = "") -> tuple[QFrame, QVBoxLayout]:
        panel = QFrame()
        panel.setObjectName("panel")
        outer = QVBoxLayout(panel)
        outer.setContentsMargins(10, 8, 10, 10)
        outer.setSpacing(8)

        head_row = QFrame()
        head_row.setObjectName("panelHeader")
        head_row.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        head = QHBoxLayout(head_row)
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(8)
        label = QLabel(title)
        label.setObjectName("panelTitle")
        label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        head.addWidget(label)
        head.addStretch()
        if pill_text:
            head.addWidget(self._pill(pill_text, "info"))
        outer.addWidget(head_row)

        body = QVBoxLayout()
        body.setSpacing(6)
        outer.addLayout(body)
        return panel, body

    def _button(self, text: str, tone: str = "", handler: object | None = None, enabled: bool = True) -> QPushButton:
        button = QPushButton(text)
        button.setProperty("tone", tone)
        button.setEnabled(enabled)
        button.setMinimumHeight(30)
        button.clicked.connect(lambda checked=False, label=text: self._button_feedback(label))
        if handler is not None:
            button.clicked.connect(handler)  # type: ignore[arg-type]
        return button

    def _badge(self, text: str) -> QLabel:
        badge = QLabel(text)
        badge.setObjectName("accountBadge")
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setFixedSize(34, 28)
        badge.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        return badge

    def _pill(self, text: str, tone: str = "") -> QLabel:
        label = QLabel(text)
        label.setObjectName("pill")
        label.setProperty("tone", tone)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setMinimumHeight(22)
        label.setMaximumHeight(24)
        label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        return label

    def _metric_row(self, label: str, value: str) -> QWidget:
        row = QFrame()
        row.setObjectName("metricRow")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(8, 5, 8, 5)
        layout.addWidget(QLabel(label))
        layout.addStretch()
        strong = QLabel(value)
        strong.setObjectName("metricValue")
        layout.addWidget(strong)
        return row

    def _gate_item(self, name: str, state: str, detail: str) -> QWidget:
        item = QFrame()
        item.setObjectName("gateItem")
        layout = QVBoxLayout(item)
        layout.setContentsMargins(10, 8, 10, 8)
        top = QHBoxLayout()
        top.addWidget(QLabel(name))
        top.addStretch()
        top.addWidget(self._pill(state, ""))
        layout.addLayout(top)
        hint = QLabel(detail)
        hint.setObjectName("hint")
        layout.addWidget(hint)
        return item

    def _turnover_item(self, account_id: str) -> QWidget:
        item = QFrame()
        item.setObjectName("turnoverItem")
        item.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout = QHBoxLayout(item)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(8)
        title = QLabel(account_id)
        title.setObjectName("metricTitle")
        value = QLabel("0")
        value.setObjectName("turnoverValue")
        hint = QLabel("实际下注累计")
        hint.setObjectName("hint")
        layout.addWidget(title)
        layout.addWidget(hint, 1)
        layout.addWidget(value)
        self.turnover_labels[account_id] = {"title": title, "value": value}
        return item

    def _pnl_item(self, account_id: str) -> QWidget:
        item = QFrame()
        item.setObjectName("pnlRow")
        item.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        item.setMinimumHeight(112)
        layout = QVBoxLayout(item)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(3)

        top = QHBoxLayout()
        name = QLabel(account_id)
        name.setObjectName("metricTitle")
        profit = QLabel("盈亏 -")
        profit.setObjectName("profitValue")
        top.addWidget(name)
        top.addStretch()
        top.addWidget(profit)
        layout.addLayout(top)

        detail = QLabel("初始 -   当前 -")
        detail.setObjectName("hint")
        detail.setVisible(False)

        self.pnl_rows[account_id] = {"name": name, "profit": profit, "detail": detail}
        return item

    def _enrich_pnl_item(self, item: QWidget, account_id: str) -> None:
        layout = item.layout()
        if not isinstance(layout, QVBoxLayout):
            return
        row = self.pnl_rows.get(account_id)
        if not row:
            return
        initial = QLabel("初始 -")
        initial.setObjectName("hint")
        current = QLabel("当前 -")
        current.setObjectName("hint")
        deposit = QLabel("累计充值 0.00")
        deposit.setObjectName("hint")
        withdraw = QLabel("累计提现 0.00")
        withdraw.setObjectName("hint")
        metrics = QGridLayout()
        metrics.setHorizontalSpacing(6)
        metrics.setVerticalSpacing(2)
        metrics.addWidget(initial, 0, 0)
        metrics.addWidget(current, 0, 1)
        metrics.addWidget(deposit, 1, 0)
        metrics.addWidget(withdraw, 1, 1)
        metrics.setColumnStretch(0, 1)
        metrics.setColumnStretch(1, 1)
        layout.addLayout(metrics)

        adjust_row = QHBoxLayout()
        adjust_row.setSpacing(5)
        deposit_input = QLineEdit()
        deposit_input.setPlaceholderText("充值金额")
        deposit_btn = self._button("记充值", "", lambda checked=False, account_id=account_id: self._on_add_deposit(account_id))
        deposit_btn.setMinimumWidth(56)
        adjust_row.addWidget(deposit_input, 1)
        adjust_row.addWidget(deposit_btn)
        withdraw_input = QLineEdit()
        withdraw_input.setPlaceholderText("提现金额")
        withdraw_btn = self._button(
            "记提现",
            "",
            lambda checked=False, account_id=account_id: self._on_add_withdraw(account_id),
        )
        withdraw_btn.setMinimumWidth(56)
        adjust_row.addWidget(withdraw_input, 1)
        adjust_row.addWidget(withdraw_btn)
        layout.addLayout(adjust_row)

        row.update(
            {
                "initial": initial,
                "current": current,
                "deposit": deposit,
                "withdraw": withdraw,
                "deposit_input": deposit_input,
                "withdraw_input": withdraw_input,
                "deposit_btn": deposit_btn,
                "withdraw_btn": withdraw_btn,
            }
        )

    def _health_box(self, value: str, label: str, key: str = "") -> QWidget:
        box = QFrame()
        box.setObjectName("healthBox")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(2)
        number = QLabel(value)
        number.setObjectName("healthValue")
        if key:
            self.health_labels[key] = number
        text = QLabel(label)
        text.setObjectName("hint")
        layout.addWidget(number)
        layout.addWidget(text)
        return box

    def _outcome_metric_box(self, value: str, label: str, key: str) -> QWidget:
        box = QFrame()
        box.setObjectName("outcomeBox")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(3)
        number = QLabel(value)
        number.setObjectName("outcomeValue")
        number.setAlignment(Qt.AlignmentFlag.AlignCenter)
        text = QLabel(label)
        text.setObjectName("hint")
        text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(number, 1)
        layout.addWidget(text)
        self.outcome_labels[key] = number
        return box

    def _outcome_count_row(self, label: str, value: str, key: str) -> QWidget:
        row = QFrame()
        row.setObjectName("outcomeCountRow")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(8, 5, 8, 5)
        layout.setSpacing(6)
        title = QLabel(label)
        title.setObjectName("hint")
        number = QLabel(value)
        number.setObjectName("metricValue")
        number.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(title, 1)
        layout.addWidget(number)
        self.outcome_labels[key] = number
        return row

    def _advanced_title(self, title: str, pill: str) -> QWidget:
        row = QFrame()
        row.setObjectName("advancedTitle")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        label = QLabel(title)
        label.setObjectName("sectionTitle")
        layout.addWidget(label)
        layout.addStretch()
        layout.addWidget(self._pill(pill, "info"))
        return row

    def _mode_banner(self) -> QWidget:
        banner = QFrame()
        banner.setObjectName("modeBanner")
        layout = QHBoxLayout(banner)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.addWidget(QLabel("轻量模式"))
        layout.addStretch()
        layout.addWidget(QLabel("低频轮询"))
        return banner

    def _build_room_selector(self) -> QWidget:
        selector = QFrame()
        selector.setObjectName("roomSelector")
        layout = QHBoxLayout(selector)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(6)
        label = QLabel("进房")
        label.setObjectName("hint")
        layout.addWidget(label)

        group = QButtonGroup(selector)
        group.setExclusive(True)
        for room_index in (1, 2, 3):
            button = self._button(f"{room_index}房", "segment", None)
            button.setCheckable(True)
            button.setMinimumWidth(46)
            button.clicked.connect(lambda checked=False, value=room_index: self._set_room_index(value))
            group.addButton(button)
            self.room_index_buttons.setdefault(room_index, []).append(button)
            layout.addWidget(button)
        self._sync_room_buttons()
        return selector

    def _selected_room_index(self) -> int:
        try:
            room_index = int(self.controller.config.room_index)
        except (TypeError, ValueError):
            room_index = 1
        return max(1, min(3, room_index))

    def _set_room_index(self, room_index: int, log: bool = True) -> None:
        room_index = max(1, min(3, int(room_index)))
        if room_index != self._selected_room_index():
            self.controller.apply_execution_config({"room_index": room_index})
        self._sync_room_buttons()
        if log:
            self._append_log(f"进房房间已选择: {room_index}房")

    def _sync_room_buttons(self) -> None:
        selected = self._selected_room_index()
        for room_index, buttons in self.room_index_buttons.items():
            for button in buttons:
                button.setChecked(room_index == selected)

    def _spin(self, low: int, high: int, value: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(low, high)
        spin.setValue(value)
        spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        return spin

    def _scroll_page(self, page: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(page)
        return scroll

    def _table(self, headers: list[str], rows: int) -> QTableWidget:
        table = QTableWidget(rows, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        table.setAlternatingRowColors(True)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setFixedHeight(26)
        table.verticalHeader().setDefaultSectionSize(24)
        table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        table.setMinimumHeight(128)
        table.setWordWrap(False)
        table.setTextElideMode(Qt.TextElideMode.ElideRight)
        return table

    def _seed_round_table(self) -> None:
        if self.round_table is None:
            return
        rows = [
            ["#01", "-", "200ms", "-", "-", "-", "-", "-", "待测"],
            ["#02", "-", "250ms", "-", "-", "-", "-", "-", "待测"],
            ["#03", "-", "300ms", "-", "-", "-", "-", "-", "待测"],
        ]
        self._fill_table(self.round_table, rows)

    def _seed_records_table(self) -> None:
        rows = [
            ["#01", "-", "-", "200ms", "-", "0", "待测"],
            ["#02", "-", "-", "250ms", "-", "0", "待测"],
            ["#03", "-", "-", "300ms", "-", "0", "待测"],
            ["#04", "-", "-", "200ms", "-", "0", "待测"],
            ["#05", "-", "-", "250ms", "-", "0", "待测"],
        ]
        self._fill_table(self.records_table, rows)

    def _fill_table(self, table: QTableWidget, rows: list[list[str]]) -> None:
        table.setRowCount(len(rows))
        for row_idx, row in enumerate(rows):
            for col_idx, value in enumerate(row):
                item = QTableWidgetItem(value)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                table.setItem(row_idx, col_idx, item)

    def _bind_controller(self) -> None:
        self.controller.on("platform_summary_updated", self._on_platform_summary_updated)
        self.controller.on("account_status_updated", self._on_account_status_updated)
        self.controller.on("operator_log_appended", self._on_log_appended)
        self.controller.on("main_account_changed", self._on_main_account_changed)
        self.controller.on("error_banner_updated", self._on_error_banner)
        self.controller.on("gate_status_updated", self._on_gate_status)
        self.controller.on("round_results_updated", self._on_round_results_updated)
        self.controller.on("hedge_plan_updated", self._on_hedge_plan_updated)
        self.controller.on("plan_account_states_updated", self._on_plan_account_states_updated)

    def _on_platform_summary_updated(self, payload: object) -> None:
        self._refresh_platform_summary()

    def _on_gate_status(self, payload: object) -> None:
        if isinstance(payload, str):
            self.run_state_label.setText(payload)

    def _on_log_appended(self, message: object) -> None:
        if isinstance(message, str):
            self._append_log(message)

    def _on_main_account_changed(self, account_id: object) -> None:
        if isinstance(account_id, str):
            self._sync_main_account(account_id)

    def _on_save_config(self) -> None:
        if not self._collect_slot_fields():
            return
        self.controller.apply_execution_config(self._execution_updates())
        self.controller.save_config()
        self._refresh_platform_summary()

    def _on_apply_advanced(self) -> None:
        self.controller.apply_execution_config(self._execution_updates())
        self._refresh_plan(self.controller.main_account)
        self._append_log("高级参数已应用")

    def _sync_form_to_controller(self) -> bool:
        if not self._collect_slot_fields():
            return False
        self.controller.apply_execution_config(self._execution_updates())
        return True

    def _on_open_login_pages(self) -> None:
        if self._sync_form_to_controller():
            self.controller.open_login_pages_clicked()

    def _on_fill_login(self) -> None:
        if self._sync_form_to_controller():
            self.controller.batch_fill_login_clicked()

    def _on_start(self) -> None:
        if self._sync_form_to_controller():
            if self._run_started_at is None:
                self._run_started_at = monotonic()
            self._refresh_runtime_elapsed()
            self.controller.start_clicked()

    def _poll_runtime_events(self) -> None:
        self.controller.poll_runtime_events()
        self._refresh_runtime_elapsed()

    def _refresh_runtime_elapsed(self) -> None:
        label = self.health_labels.get("runtime")
        if label is None:
            return
        if self._run_started_at is None:
            label.setText("00:00:00")
            return
        elapsed = max(0, int(monotonic() - self._run_started_at))
        hours, remainder = divmod(elapsed, 3600)
        minutes, seconds = divmod(remainder, 60)
        label.setText(f"{hours:02d}:{minutes:02d}:{seconds:02d}")

    def _on_plan_state_action(self, account_id: str) -> None:
        current = self._plan_account_states.get(account_id, "normal")
        next_state = {
            "normal": "pending_exclude",
            "pending_exclude": "normal",
            "excluded": "pending_restore",
            "pending_restore": "excluded",
            "restore_failed": "pending_restore",
        }.get(current, "pending_exclude")
        self.controller.set_plan_account_state(account_id, next_state)

    def _on_plan_account_states_updated(self, payload: object) -> None:
        if not isinstance(payload, dict):
            return
        for account_id in ACCOUNT_IDS:
            self._plan_account_states[account_id] = str(payload.get(account_id) or "normal")
            self._refresh_plan_account_state(account_id)

    def _advance_plan_account_states(self) -> None:
        for account_id, state in list(self._plan_account_states.items()):
            if state == "pending_exclude":
                self._plan_account_states[account_id] = "excluded"
            elif state == "pending_restore":
                self._plan_account_states[account_id] = "normal"
            self._refresh_plan_account_state(account_id)

    def _refresh_plan_account_state(self, account_id: str) -> None:
        row = self.plan_rows.get(account_id)
        if not row:
            return
        state = self._plan_account_states.get(account_id, "normal")
        state_text, action_text = {
            "normal": ("正常", "下局剔除"),
            "pending_exclude": ("待剔除", "取消剔除"),
            "excluded": ("已剔除", "下局恢复"),
            "pending_restore": ("待恢复", "取消恢复"),
            "restore_failed": ("恢复失败", "下局恢复"),
        }.get(state, ("正常", "下局剔除"))
        state_label = row.get("state")
        action_button = row.get("action")
        if isinstance(state_label, QLabel):
            state_label.setText(state_text)
            state_label.setProperty("tone", "warn" if state in {"pending_exclude", "pending_restore"} else "")
            self._refresh_widget_style(state_label)
        if isinstance(action_button, QPushButton):
            action_button.setText(action_text)

    def _on_handoff(self) -> None:
        if self._sync_form_to_controller():
            self.controller.batch_handoff_clicked()

    def _on_account_restart(self, account_id: str) -> None:
        if self._sync_form_to_controller():
            self.controller.restart_account_clicked(account_id)

    def _on_account_handoff(self, account_id: str) -> None:
        if self._sync_form_to_controller():
            self.controller.handoff_account_clicked(account_id)

    def _on_account_enter_room(self, account_id: str) -> None:
        if self._sync_form_to_controller():
            self.controller.enter_room_account_clicked(account_id, self._selected_room_index())

    def _on_enter_room(self) -> None:
        if self._sync_form_to_controller():
            self.controller.batch_enter_room_clicked(self._selected_room_index())

    def _on_enter_room_all(self) -> None:
        if self._sync_form_to_controller():
            self.controller.enter_room_all_clicked(self._selected_room_index())

    def _on_refresh_headless(self) -> None:
        if self._sync_form_to_controller():
            self.controller.refresh_headless_clicked()

    def _on_release(self) -> None:
        if self._sync_form_to_controller():
            self.controller.batch_release_clicked()

    def _on_strategy_preview_changed(self, _value: int = 0) -> None:
        self._refresh_plan(self.controller.main_account)

    def _protected_notice(self) -> None:
        self._append_log("该动作属于待二次审核功能，当前未接入真实链路")

    def _button_feedback(self, label: str) -> None:
        if hasattr(self, "run_state_label"):
            self.run_state_label.setText(f"已点击：{label}")
        if hasattr(self, "main_summary"):
            self.main_summary.setText(f"当前主号 {self.controller.main_account} · 已点击 {label}")

    def _execution_updates(self) -> dict[str, int]:
        return {
            "amount_min": self.amount_min_input.value(),
            "amount_max": self.amount_max_input.value(),
            "min_balance_yuan": self.min_balance_input.value(),
            "main_successor_account": str(self.main_successor_input.currentData() or ""),
            "click_interval_ms": self.click_interval_input.value(),
            "min_countdown": self.min_countdown_input.value(),
            "confirm_ms": self.confirm_ms_input.value(),
            "room_index": self._selected_room_index(),
        }

    def _collect_slot_fields(self) -> bool:
        is_valid = True
        for account_id, widgets in self.slot_cards.items():
            updates: dict[str, str] = {}
            for key in [
                "display_name",
                "login_url",
                "account_username",
                "account_password",
                "proxy_bundle",
            ]:
                widget = widgets.get(key)
                if isinstance(widget, QLineEdit):
                    updates[key] = widget.text().strip()
            proxy_bundle = updates.get("proxy_bundle", "")
            if proxy_bundle:
                try:
                    updates.update(parse_proxy_bundle_line(proxy_bundle))
                except ValueError:
                    self._append_log(f"{account_id} 代理格式错误，应为：IP|端口|账号|密码|到期时间")
                    is_valid = False
                    continue
            else:
                updates.update(
                    {
                        "proxy_host": "",
                        "proxy_port": "",
                        "proxy_username": "",
                        "proxy_password": "",
                        "proxy_expire_at": "",
                    }
                )
            if updates:
                self.controller.update_platform_slot(account_id, updates)
        return is_valid

    def _refresh_from_controller(self) -> None:
        config = self.controller.config
        self.amount_min_input.setValue(config.amount_min)
        self.amount_max_input.setValue(config.amount_max)
        self.min_balance_input.setValue(config.min_balance_yuan)
        successor = str(config.main_successor_account or "")
        successor_index = self.main_successor_input.findData(successor)
        self.main_successor_input.setCurrentIndex(successor_index if successor_index >= 0 else 0)
        self.click_interval_input.setValue(config.click_interval_ms)
        self.min_countdown_input.setValue(config.min_countdown)
        self.confirm_ms_input.setValue(config.confirm_ms)

        for slot in self.controller.platform_slots:
            widgets = self.slot_cards.get(slot.account_id, {})
            for key in [
                "display_name",
                "login_url",
                "account_username",
                "account_password",
                "proxy_bundle",
            ]:
                widget = widgets.get(key)
                if isinstance(widget, QLineEdit):
                    if key == "proxy_bundle":
                        value = slot.proxy_bundle
                        if not value and all([slot.proxy_host, slot.proxy_port, slot.proxy_username, slot.proxy_password, slot.proxy_expire_at]):
                            value = "|".join([slot.proxy_host, slot.proxy_port, slot.proxy_username, slot.proxy_password, slot.proxy_expire_at])
                        widget.setText(value)
                        widget.setCursorPosition(0)
                    else:
                        widget.setText(str(getattr(slot, key)))
        self._sync_main_account(config.main_account)
        self._sync_room_buttons()
        self._refresh_platform_summary()
        self._on_account_status_updated(self.controller.account_status)
        self._on_hedge_plan_updated(self.controller.current_plan)
        self._on_round_results_updated(self.controller.round_results)

    def _sync_main_account(self, main_account: str) -> None:
        self.main_summary.setText(f"当前主号 {main_account}")
        for account_id, button in self.role_buttons.items():
            button.setChecked(account_id == main_account)
        subs = [account_id for account_id in ACCOUNT_IDS if account_id != main_account]
        self.handoff_btn.setText(f"{'/'.join(subs)} 接管")
        self._sync_role_labels(main_account)
        self._refresh_plan(main_account)

    def _sync_role_labels(self, main_account: str) -> None:
        for account_id in ACCOUNT_IDS:
            is_main = account_id == main_account
            summary = self.summary_cards.get(account_id, {})
            if summary:
                summary["role"].setText("当前主号 · 有头观察" if is_main else "副号 · 无头执行")
                summary["state"].setText("主号" if is_main else "副号")
                summary["state"].setProperty("tone", "good" if is_main else "")
            card = self.account_cards.get(account_id, {})
            if card:
                card["role"].setText("当前主号 · 有头观察" if is_main else "副号 · 无头执行")

    def _refresh_platform_summary(self) -> None:
        for slot in self.controller.platform_slots:
            card = self.summary_cards.get(slot.account_id)
            if not card:
                continue
            display_name = slot.display_name or f"平台 {slot.account_id}"
            expire = slot.proxy_expire_at or "-"
            account = slot.account_username or "-"
            card["name"].setText(display_name)
            card["expire"].setText(f"代理到期：{expire}")
            card["account"].setText(f"平台账号：{account}")
            title = f"{slot.account_id} · {display_name}" if display_name else slot.account_id
            turnover = self.turnover_labels.get(slot.account_id)
            if turnover:
                turnover["title"].setText(title)
            pnl = self.pnl_rows.get(slot.account_id)
            if pnl:
                pnl["name"].setText(title)

    def _refresh_plan(self, main_account: str) -> None:
        amount_min = self.amount_min_input.value()
        amount_max = self.amount_max_input.value()
        click_interval = self.click_interval_input.value()
        min_countdown = self.min_countdown_input.value()
        confirm_ms = self.confirm_ms_input.value()
        for account_id, labels in self.plan_rows.items():
            is_main = account_id == main_account
            labels["amount"].setText(f"{amount_min}-{amount_max}" if is_main else "分摊")
            labels["side"].setText(f"{account_id} · {'主单方向' if is_main else '副号对冲'}")
            labels["chips"].setText(
                f"点击 {click_interval}ms · 倒计时≥{min_countdown}s · 确认{confirm_ms}ms"
                if is_main
                else f"等待主单后分摊 · 点击 {click_interval}ms"
            )
            labels["role"].setText("主" if is_main else "副")
            labels["amount"].setProperty("role", "main" if is_main else "")
            self._refresh_widget_style(labels["amount"])
            self._refresh_plan_account_state(account_id)

    def _on_account_status_updated(self, payload: object) -> None:
        if not isinstance(payload, list):
            return
        for summary in payload:
            account_id = getattr(summary, "account_id", "")
            card = self.account_cards.get(account_id)
            if not card:
                continue
            state_label = str(getattr(summary, "state_label", "等待") or "等待")
            card["state"].setText(state_label)
            betting_text = state_label if state_label in {"可下注", "不可下注", "大厅", "异常"} else "不可下注"
            if state_label.startswith("数据过期"):
                betting_text = "数据过期"
            card["betting_zone"].setText(betting_text)
            betting_open = bool(getattr(summary, "betting_open", False))
            countdown = getattr(summary, "countdown", None)
            if betting_open and isinstance(countdown, int):
                card["betting_zone"].setText(f"可下注 {countdown}秒")
            elif betting_open:
                card["betting_zone"].setText("可下注")
            elif state_label in {"大厅", "异常", "数据过期"} or state_label.startswith("数据过期"):
                card["betting_zone"].setText(state_label)
            else:
                card["betting_zone"].setText("不可下注")
            card["betting_zone"].setProperty("tone", "betting" if betting_open else "")
            self._refresh_widget_style(card["betting_zone"])
            card["room"].setText(f"房间：{getattr(summary, 'room_label', '-') or '-'}")
            card["round"].setText(f"局号：{getattr(summary, 'round_id', '-') or '-'}")
            balance = getattr(summary, "balance", None)
            card["balance"].setText(f"余额：{balance if balance is not None else '-'}")
            round_id = self._public_round_key(getattr(summary, "round_id", ""))
            if round_id:
                self._account_round_ids[account_id] = round_id
            if "target_room" in card:
                card["target_room"].setText(f"目标：{getattr(summary, 'target_room_label', '-') or '-'}")
            if "room_progress" in card:
                card["room_progress"].setText(f"进度：{getattr(summary, 'room_entry_detail', '') or '-'}")
            balance_value = self._decimal_or_none(balance)
            if balance_value is not None:
                self._current_balances[account_id] = balance_value
                if self._profit_tracking_active and account_id not in self._initial_balances:
                    self._initial_balances[account_id] = balance_value
                self._settle_outcome_pending(account_id, balance_value, current_round_id=round_id)
        self._refresh_profit_display()

    def _format_money_value(self, value: object) -> str:
        text = str(value if value is not None else "0").strip()
        if text.endswith(".00"):
            return text[:-3]
        return text or "0"

    def _status_text(self, status: object) -> str:
        return {
            "complete": "完整",
            "incomplete": "缺口",
            "error": "异常",
            "empty": "无结果",
            "unknown": "未知",
        }.get(str(status or "").lower(), str(status or "-"))

    def _decimal_or_none(self, value: object) -> Decimal | None:
        if value is None or value == "":
            return None
        if isinstance(value, Decimal):
            return value
        try:
            return Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            return None

    def _format_balance_value(self, value: Decimal | None) -> str:
        if value is None:
            return "-"
        return f"{value.quantize(Decimal('0.01')):.2f}"

    def _format_profit_value(self, value: Decimal | None) -> str:
        if value is None:
            return "盈亏 -"
        sign = "+" if value >= 0 else ""
        return f"盈亏 {sign}{self._format_balance_value(value)}"

    def _refresh_widget_style(self, widget: QWidget) -> None:
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        widget.update()

    def _reset_profit_tracking(self) -> None:
        self._profit_tracking_active = True
        self._refresh_profit_display()

    def _refresh_profit_display_legacy(self) -> None:
        for account_id in ACCOUNT_IDS:
            row = self.pnl_rows.get(account_id)
            if not row:
                continue
            initial = self._initial_balances.get(account_id)
            current = self._current_balances.get(account_id)
            profit = current - initial if initial is not None and current is not None else None
            row["profit"].setText(self._format_profit_value(profit))
            row["detail"].setText(
                f"初始 {self._format_balance_value(initial)}   当前 {self._format_balance_value(current)}"
            )

    def _on_reset_turnover(self) -> None:
        self._turnover_reset_index = len(self.controller.round_results)
        self._refresh_turnover_display(self.controller.round_results)
        self._append_log("下注流水已重置")

    def _add_balance_adjustment(self, account_id: str, amount: Decimal, *, is_withdraw: bool) -> None:
        table = self._withdraw_totals if is_withdraw else self._deposit_totals
        if amount < 0:
            amount = -amount
        table[account_id] = table.get(account_id, Decimal("0")) + amount
        self._refresh_profit_display()

    def _on_add_deposit(self, account_id: str) -> None:
        row = self.pnl_rows.get(account_id, {})
        widget = row.get("deposit_input")
        if not isinstance(widget, QLineEdit):
            return
        amount = self._decimal_or_none(widget.text())
        if amount is None:
            return
        self._add_balance_adjustment(account_id, amount, is_withdraw=False)
        widget.setText("")

    def _on_add_withdraw(self, account_id: str) -> None:
        row = self.pnl_rows.get(account_id, {})
        widget = row.get("withdraw_input")
        if not isinstance(widget, QLineEdit):
            return
        amount = self._decimal_or_none(widget.text())
        if amount is None:
            return
        self._add_balance_adjustment(account_id, amount, is_withdraw=True)
        widget.setText("")

    def _refresh_profit_display(self) -> None:
        for account_id in ACCOUNT_IDS:
            row = self.pnl_rows.get(account_id)
            if not row:
                continue
            initial = self._initial_balances.get(account_id)
            current = self._current_balances.get(account_id)
            deposit = self._deposit_totals.get(account_id, Decimal("0"))
            withdraw = self._withdraw_totals.get(account_id, Decimal("0"))
            profit = None
            if initial is not None and current is not None:
                profit = current - initial - deposit + withdraw
            row["profit"].setText(self._format_profit_value(profit))
            initial_text = self._format_balance_value(initial)
            current_text = self._format_balance_value(current)
            deposit_text = self._format_balance_value(deposit)
            withdraw_text = self._format_balance_value(withdraw)
            if "initial" in row:
                row["initial"].setText(f"初始 {initial_text}")
            if "current" in row:
                row["current"].setText(f"当前 {current_text}")
            if "deposit" in row:
                row["deposit"].setText(f"累计充值 {deposit_text}")
            if "withdraw" in row:
                row["withdraw"].setText(f"累计提现 {withdraw_text}")
            row["detail"].setText(
                f"初始 {initial_text} | 当前 {current_text} | 充值 {deposit_text} | 提现 {withdraw_text}"
            )
            if profit is None:
                row["profit"].setProperty("tone", "")
            elif profit > 0:
                row["profit"].setProperty("tone", "positive")
            elif profit < 0:
                row["profit"].setProperty("tone", "negative")
            else:
                row["profit"].setProperty("tone", "")
            self._refresh_widget_style(row["profit"])

    def _public_round_key(self, value: object) -> str:
        text = str(value or "").strip()
        if not text or text in {"-", "None", "none", "null"}:
            return ""
        parts = text.split("-")
        if len(parts) >= 4 and parts[-1].strip().isdigit():
            return "-".join(part.strip() for part in parts[:3] if part.strip())
        return text

    def _outcome_round_key(self, result: object) -> str:
        round_id = self._public_round_key(getattr(result, "round_id", ""))
        round_number = str(getattr(result, "round_number", "") or "")
        return f"{round_number}:{round_id}" if round_id or round_number else str(id(result))

    def _round_leg_by_account(self, result: object) -> dict[str, dict[str, Any]]:
        rows: dict[str, dict[str, Any]] = {}
        for item in getattr(result, "legs", []) or []:
            if isinstance(item, dict):
                account_id = str(item.get("account_id") or item.get("instance_id") or "")
                if account_id:
                    rows[account_id] = item
        return rows

    def _normalized_side(self, value: object) -> str:
        text = str(value or "").strip().lower()
        if text in {"banker", "庄", "庄家"}:
            return "banker"
        if text in {"player", "闲", "闲家"}:
            return "player"
        if text in {"tie", "和"}:
            return "tie"
        return ""

    def _track_outcome_rounds(self, results: list[object]) -> None:
        for result in results:
            key = self._outcome_round_key(result)
            if key in self._outcome_recorded_rounds:
                continue
            self._outcome_recorded_rounds.add(key)
            result_rows = self._result_by_account(result)
            leg_rows = self._round_leg_by_account(result)
            main_account = self.controller.main_account
            main_result = result_rows.get(main_account)
            main_leg = leg_rows.get(main_account, {})
            if not main_result:
                continue
            main_side = self._normalized_side(
                main_leg.get("side") or main_leg.get("side_text") or main_result.get("side") or main_result.get("side_text")
            )
            main_actual = self._decimal_or_none(main_result.get("actual_amount"))
            if main_side not in {"banker", "player"} or main_actual is None or main_actual <= 0:
                continue
            banker_total = Decimal("0")
            for account_id, row in result_rows.items():
                leg = leg_rows.get(account_id, {})
                side = self._normalized_side(
                    leg.get("side") or leg.get("side_text") or row.get("side") or row.get("side_text")
                )
                if side != "banker":
                    continue
                actual = self._decimal_or_none(row.get("actual_amount"))
                if actual is not None and actual > 0:
                    banker_total += actual
            self._outcome_pending.append(
                {
                    "key": key,
                    "round_id": self._public_round_key(getattr(result, "round_id", "")),
                    "account_id": main_account,
                    "main_side": main_side,
                    "main_actual": main_actual,
                    "banker_total": banker_total,
                    "post_balance": self._current_balances.get(main_account),
                }
            )
        for account_id, balance in list(self._current_balances.items()):
            self._settle_outcome_pending(
                account_id,
                balance,
                current_round_id=self._account_round_ids.get(account_id, ""),
            )
        self._refresh_outcome_display()

    def _money_close(self, value: Decimal, target: Decimal, amount: Decimal | None = None) -> bool:
        tolerance = Decimal("0.06")
        if amount is not None:
            tolerance = max(tolerance, abs(amount) * Decimal("0.005"))
        return abs(value - target) <= tolerance

    def _infer_outcome_from_main_delta(
        self,
        *,
        side: str,
        amount: Decimal,
        delta: Decimal,
        round_changed: bool,
    ) -> str:
        if side == "banker":
            if self._money_close(delta, amount * Decimal("1.95"), amount) or self._money_close(
                delta,
                amount * Decimal("2"),
                amount,
            ):
                return "banker"
            if self._money_close(delta, amount, amount):
                return "tie"
            if round_changed and self._money_close(delta, Decimal("0"), amount):
                return "player"
        elif side == "player":
            if self._money_close(delta, amount * Decimal("2"), amount):
                return "player"
            if self._money_close(delta, amount, amount):
                return "tie"
            if round_changed and self._money_close(delta, Decimal("0"), amount):
                return "banker"
        return ""

    def _settle_outcome_pending(self, account_id: str, balance: Decimal, *, current_round_id: str = "") -> None:
        current_round = self._public_round_key(current_round_id)
        for pending in list(self._outcome_pending):
            if pending.get("account_id") != account_id:
                continue
            amount = pending.get("main_actual")
            if not isinstance(amount, Decimal) or amount <= 0:
                self._outcome_pending.remove(pending)
                continue
            post_balance = pending.get("post_balance")
            if not isinstance(post_balance, Decimal):
                pending["post_balance"] = balance
                continue
            delta = balance - post_balance
            if delta < 0 and self._money_close(delta, -amount, amount):
                pending["post_balance"] = balance
                continue
            round_id = self._public_round_key(pending.get("round_id"))
            round_changed = bool(current_round and round_id and current_round != round_id)
            outcome = self._infer_outcome_from_main_delta(
                side=str(pending.get("main_side") or ""),
                amount=amount,
                delta=delta,
                round_changed=round_changed,
            )
            if not outcome:
                continue
            self._outcome_counts[outcome] = self._outcome_counts.get(outcome, 0) + 1
            if outcome == "banker":
                banker_total = pending.get("banker_total")
                if isinstance(banker_total, Decimal) and banker_total > 0:
                    self._outcome_commission_total += banker_total * Decimal("0.05")
            self._outcome_pending.remove(pending)
        self._refresh_outcome_display()

    def _refresh_outcome_display(self) -> None:
        commission = self.outcome_labels.get("commission")
        if commission:
            commission.setText(self._format_balance_value(self._outcome_commission_total))
        for key in ("banker", "player", "tie"):
            label = self.outcome_labels.get(key)
            if label:
                label.setText(str(self._outcome_counts.get(key, 0)))

    def _refresh_turnover_display(self, results: list[object]) -> None:
        totals = {account_id: Decimal("0") for account_id in ACCOUNT_IDS}
        start = min(self._turnover_reset_index, len(results))
        for item in results[start:]:
            for row in getattr(item, "results", []) or []:
                if not isinstance(row, dict):
                    continue
                account_id = str(row.get("instance_id") or row.get("account_id") or "")
                if account_id not in totals:
                    continue
                actual = self._decimal_or_none(row.get("actual_amount"))
                if actual is not None:
                    totals[account_id] += actual
        for account_id, value in totals.items():
            labels = self.turnover_labels.get(account_id)
            if labels:
                labels["value"].setText(self._format_money_value(value))

    def _result_by_account(self, result: object) -> dict[str, dict[str, Any]]:
        rows: dict[str, dict[str, Any]] = {}
        for item in getattr(result, "results", []) or []:
            if isinstance(item, dict):
                account_id = str(item.get("instance_id") or item.get("account_id") or "")
                if account_id:
                    rows[account_id] = item
        return rows

    def _leg_by_account(self, payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
        rows: dict[str, dict[str, Any]] = {}
        legs = payload.get("legs", [])
        if isinstance(legs, list):
            for leg in legs:
                if isinstance(leg, dict):
                    account_id = str(leg.get("account_id") or leg.get("instance_id") or "")
                    if account_id:
                        rows[account_id] = leg
        return rows

    def _round_account_text(self, result: object, account_id: str) -> str:
        row = self._result_by_account(result).get(account_id)
        if not row:
            return "-"
        actual = self._format_money_value(row.get("actual_amount", 0))
        missing = self._format_money_value(row.get("missing_amount", 0))
        elapsed = row.get("click_sequence_ms") or row.get("elapsed_ms") or "-"
        status = str(row.get("status") or "").upper()
        if status == "COMPLETE" and missing in {"0", "0.0"}:
            return f"实{actual} · {elapsed}ms"
        return f"实{actual}/缺{missing} · {elapsed}ms"

    def _countdown_text(self, countdowns: object) -> str:
        if not isinstance(countdowns, dict) or not countdowns:
            return "-"
        parts = []
        for account_id in ACCOUNT_IDS:
            if account_id in countdowns:
                parts.append(f"{account_id}:{countdowns[account_id]}s")
        return " ".join(parts) if parts else "-"

    def _on_hedge_plan_updated(self, payload: object) -> None:
        if not isinstance(payload, dict) or not payload:
            return
        legs = self._leg_by_account(payload)
        excluded = payload.get("excluded_accounts", {})
        excluded_accounts = set(excluded.keys()) if isinstance(excluded, dict) else set()
        click_interval = payload.get("click_interval_ms") or self.click_interval_input.value()
        confirm_ms = payload.get("confirm_ms") or self.confirm_ms_input.value()
        round_number = payload.get("round_number") or "-"
        if hasattr(self, "plan_round_label"):
            self.plan_round_label.setText(f"计划第 {round_number} 轮")
        for account_id, labels in self.plan_rows.items():
            leg = legs.get(account_id)
            if not leg:
                if account_id in excluded_accounts:
                    labels["amount"].setText("剔除")
                    labels["side"].setText(f"{account_id} · 余额不足剔除")
                    labels["chips"].setText("本轮不参与")
                    labels["role"].setText("-")
                    labels["amount"].setProperty("role", "")
                    self._refresh_widget_style(labels["amount"])
                    self._refresh_plan_account_state(account_id)
                    continue
                labels["amount"].setText("-")
                labels["side"].setText(f"{account_id} · 等待计划")
                labels["chips"].setText("本轮未分配")
                labels["role"].setText("副" if account_id != self.controller.main_account else "主")
                labels["amount"].setProperty("role", "main" if account_id == self.controller.main_account else "")
                self._refresh_widget_style(labels["amount"])
                self._refresh_plan_account_state(account_id)
                continue
            amount = self._format_money_value(leg.get("amount", "-"))
            raw_side = str(leg.get("side_text") or leg.get("side") or "-")
            side = SIDE_TEXT_CN.get(raw_side, raw_side)
            role = str(leg.get("role") or "")
            chips = leg.get("chips", [])
            chip_text = "+".join(str(item) for item in chips) if isinstance(chips, list) else str(chips or "-")
            labels["amount"].setText(amount)
            labels["side"].setText(f"{account_id} · {'主单方向' if role == 'main' else '副号对冲'} · {side}")
            labels["chips"].setText(f"筹码 {chip_text} · 点击 {click_interval}ms · 确认 {confirm_ms}ms")
            labels["role"].setText("主" if role == "main" else "副")
            labels["amount"].setProperty("role", "main" if role == "main" else "")
            self._refresh_widget_style(labels["amount"])
            self._refresh_plan_account_state(account_id)

    def _on_round_results_updated(self, payload: object) -> None:
        if not isinstance(payload, list):
            return
        results = list(payload)
        self._refresh_turnover_display(results)
        self._track_outcome_rounds(results)
        if not results:
            return
        recent = list(reversed(results[-10:]))
        round_rows: list[list[str]] = []
        record_rows: list[list[str]] = []
        total_missing = 0
        max_round_ms = 0
        max_click_ms = 0
        for item in results:
            try:
                total_missing += int(getattr(item, "missing_total", 0) or 0)
            except (TypeError, ValueError):
                pass
            try:
                max_round_ms = max(max_round_ms, int(getattr(item, "elapsed_ms", 0) or 0))
                max_click_ms = max(max_click_ms, int(getattr(item, "max_elapsed_ms", 0) or 0))
            except (TypeError, ValueError):
                pass
        for item in recent:
            round_no = f"#{int(getattr(item, 'round_number', 0) or 0):02d}"
            countdown = self._countdown_text(getattr(item, "send_countdowns", {}))
            interval = f"{getattr(item, 'click_interval_ms', '-') }ms"
            elapsed = f"{getattr(item, 'elapsed_ms', '-') }ms"
            missing = self._format_money_value(getattr(item, "missing_total", 0))
            status = self._status_text(getattr(item, "status", ""))
            round_rows.append(
                [
                    round_no,
                    countdown,
                    interval,
                    self._round_account_text(item, "a1"),
                    self._round_account_text(item, "a2"),
                    self._round_account_text(item, "a3"),
                    self._round_account_text(item, "a4"),
                    elapsed,
                    missing,
                ]
            )
            record_rows.append(
                [
                    round_no,
                    str(getattr(item, "room_label", "") or "-"),
                    countdown,
                    interval,
                    elapsed,
                    missing,
                    status,
                ]
            )
        if self.round_table is not None:
            self._fill_table(self.round_table, round_rows)
        self._fill_table(self.records_table, record_rows)
        if "rounds" in self.health_labels:
            self.health_labels["rounds"].setText(str(len(results)))
        if "missing" in self.health_labels:
            self.health_labels["missing"].setText(str(total_missing))
        if "max_round_ms" in self.health_labels:
            self.health_labels["max_round_ms"].setText(f"{max_round_ms}ms" if max_round_ms else "-")
        if "max_click_ms" in self.health_labels:
            self.health_labels["max_click_ms"].setText(f"{max_click_ms}ms" if max_click_ms else "-")

    def _append_log(self, message: str) -> None:
        self._ui_logs.append(message)
        if len(self._ui_logs) > 200:
            self._ui_logs = self._ui_logs[-200:]
        if hasattr(self, "log_view"):
            self.log_view.setPlainText("\n".join(self._ui_logs))
            self.log_view.moveCursor(self.log_view.textCursor().MoveOperation.End)

    def _on_error_banner(self, message: object) -> None:
        if isinstance(message, str):
            self._append_log(message)

    def _apply_style(self) -> None:
        stylesheet = (
            """
            QMainWindow, QWidget {
                background: #eef3f8;
                color: #172033;
                font-family: "__FONT_FAMILY__", "Microsoft YaHei", "Segoe UI", sans-serif;
                font-size: 13px;
            }
            QFrame#topbar, QFrame#panel, QGroupBox#panel {
                background: #ffffff;
                border: 1px solid #d7e1ea;
                border-radius: 8px;
            }
            QFrame#topbar {
                border-color: #c9d7e4;
            }
            QLabel#brandMark {
                min-width: 38px;
                min-height: 38px;
                max-width: 38px;
                max-height: 38px;
                border-radius: 8px;
                background: #1f6feb;
                color: white;
                font-weight: 700;
            }
            QLabel#appTitle {
                font-size: 20px;
                font-weight: 700;
                color: #142033;
            }
            QLabel#subline, QLabel#hint {
                color: #617083;
                font-size: 12px;
            }
            QLabel#panelTitle {
                font-size: 15px;
                font-weight: 700;
            }
            QLabel#sectionTitle, QLabel#cardTitle {
                font-weight: 700;
            }
            QLabel#accountBadge {
                min-width: 34px;
                min-height: 28px;
                max-height: 28px;
                border-radius: 7px;
                background: #e7eef6;
                color: #17406f;
                font-weight: 700;
            }
            QLabel#pill {
                min-height: 22px;
                padding: 1px 8px;
                border-radius: 11px;
                background: #e8eef5;
                color: #405064;
                font-weight: 600;
            }
            QLabel#pill[tone="good"] {
                background: #dff7ea;
                color: #087443;
            }
            QLabel#pill[tone="info"] {
                background: #e6f1ff;
                color: #175cd3;
            }
            QLabel#pill[tone="warn"] {
                background: #fff4d6;
                color: #936600;
            }
            QLabel#countdown {
                font-size: 26px;
                font-weight: 700;
                color: #175cd3;
            }
            QLabel#countdown[tone="betting"] {
                color: #138a43;
            }
            QLabel#amount, QLabel#healthValue {
                font-size: 20px;
                font-weight: 700;
                color: #0f172a;
            }
            QLabel#metricTitle {
                color: #0f172a;
                font-weight: 700;
            }
            QLabel#turnoverValue {
                color: #175cd3;
                font-size: 20px;
                font-weight: 800;
            }
            QLabel#profitValue {
                color: #0f172a;
                font-size: 16px;
                font-weight: 800;
            }
            QLabel#profitValue[tone="positive"] {
                color: #1659D8;
            }
            QLabel#profitValue[tone="negative"] {
                color: #D92F2F;
            }
            QLabel#outcomeValue {
                color: #0f172a;
                font-size: 20px;
                font-weight: 800;
            }
            QLabel#planAmount {
                background: #eef3f8;
                border: 1px solid #e3ebf3;
                border-radius: 7px;
                color: #0f172a;
                font-size: 17px;
                font-weight: 700;
                min-height: 38px;
            }
            QLabel#planAmount[role="main"] {
                background: #ead18a;
                border-color: #c49a2c;
                color: #3d2b00;
            }
            QLabel#planTitle {
                color: #0f172a;
                font-weight: 700;
            }
            QLabel#planMeta {
                color: #536275;
                font-size: 12px;
            }
            QPushButton {
                background: #eef4fa;
                border: 1px solid #cbd8e5;
                border-radius: 7px;
                padding: 4px 10px;
                color: #172033;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #e3edf7;
            }
            QPushButton:pressed {
                background: #cbd8e5;
                border-color: #9fb2c5;
                padding-top: 6px;
                padding-left: 12px;
            }
            QPushButton:disabled {
                color: #8a98a8;
                background: #edf2f7;
                border-color: #d9e2ec;
            }
            QPushButton[tone="primary"] {
                background: #2563eb;
                color: white;
                border-color: #2563eb;
            }
            QPushButton[tone="primary"]:pressed {
                background: #1746b8;
                border-color: #1746b8;
            }
            QPushButton[tone="success"] {
                background: #16865a;
                color: white;
                border-color: #16865a;
            }
            QPushButton[tone="success"]:pressed {
                background: #0f6845;
                border-color: #0f6845;
            }
            QPushButton[tone="warn"] {
                background: #f59e0b;
                color: #241a04;
                border-color: #f59e0b;
            }
            QPushButton[tone="warn"]:pressed {
                background: #c57c04;
                border-color: #c57c04;
            }
            QPushButton[tone="danger"] {
                background: #dc2626;
                color: white;
                border-color: #dc2626;
            }
            QPushButton[tone="danger"]:pressed {
                background: #aa1f1f;
                border-color: #aa1f1f;
            }
            QPushButton[tone="segment"] {
                min-width: 54px;
                background: #edf3f9;
            }
            QPushButton[tone="segment"]:checked {
                background: #2563eb;
                color: white;
                border-color: #2563eb;
            }
            QTabWidget::pane {
                border: 0;
            }
            QTabBar::tab {
                background: #dfe8f2;
                border: 1px solid #cad7e5;
                border-radius: 7px;
                padding: 8px 20px;
                margin-right: 6px;
                font-weight: 700;
            }
            QTabBar::tab:selected {
                background: #2563eb;
                color: white;
                border-color: #2563eb;
            }
            QLineEdit, QSpinBox {
                background: white;
                border: 1px solid #ccd8e5;
                border-radius: 6px;
                min-height: 24px;
                padding: 2px 7px;
            }
            QTextEdit#logView, QTableWidget {
                background: #fbfdff;
                border: 1px solid #d9e2ec;
                border-radius: 6px;
            }
            QFrame#summaryCard, QFrame#accountCard, QFrame#gateItem,
            QFrame#turnoverItem, QFrame#pnlRow, QFrame#planLine, QFrame#healthBox, QFrame#metricRow,
            QFrame#outcomeBox, QFrame#outcomeCountRow,
            QFrame#batchStrip, QFrame#modeBanner, QFrame#roomSelector {
                background: #f8fbfe;
                border: 1px solid #d9e4ee;
                border-radius: 8px;
            }
            QFrame#modeBanner {
                background: #eefaf5;
                border-color: #bfe8d4;
            }
            QFrame#advancedTitle {
                background: transparent;
                border: 0;
            }
            QLabel#tableHeader {
                color: #536275;
                font-weight: 700;
                font-size: 12px;
            }
            QLabel#metricValue {
                font-weight: 700;
            }
            """
        )
        self.setStyleSheet(stylesheet.replace("__FONT_FAMILY__", self._ui_font_family))

    def _install_ui_font(self) -> str:
        font_candidates = [
            Path("C:/Windows/Fonts/NotoSansSC-VF.ttf"),
            Path("C:/Windows/Fonts/SourceHanSansCN-Regular.otf"),
            Path("C:/Windows/Fonts/msyh.ttc"),
            Path("C:/Windows/Fonts/simhei.ttf"),
        ]
        for font_path in font_candidates:
            if not font_path.exists():
                continue
            font_id = QFontDatabase.addApplicationFont(str(font_path))
            if font_id < 0:
                continue
            families = QFontDatabase.applicationFontFamilies(font_id)
            if not families:
                continue
            family = families[0]
            font = QFont(family, 10)
            app = QApplication.instance()
            if app is not None:
                app.setFont(font)
            self.setFont(font)
            return family
        return "Microsoft YaHei"
