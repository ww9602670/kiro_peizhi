"""A lightweight phase-1/2/3 hedge UI dashboard."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QFont, QFontDatabase
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QAbstractSpinBox,
    QApplication,
    QButtonGroup,
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
        self.account_cards: dict[str, dict[str, QLabel]] = {}
        self.plan_rows: dict[str, dict[str, QLabel]] = {}

        root = QWidget()
        self.setCentralWidget(root)
        self._build_ui(root)
        self._apply_style()
        self._bind_controller()
        self._refresh_from_controller()
        self._runtime_poll_timer = QTimer(self)
        self._runtime_poll_timer.setInterval(1200)
        self._runtime_poll_timer.timeout.connect(self.controller.poll_runtime_events)
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
        batch_layout.addWidget(self._button("批量进房", "success", self._on_enter_room))
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

        left = QVBoxLayout()
        left.setSpacing(12)
        left.addWidget(self._build_run_control_panel())
        left.addWidget(self._build_advanced_panel())
        left.addStretch()
        layout.addLayout(left, 1)

        center = QVBoxLayout()
        center.setSpacing(12)
        center.addWidget(self._build_account_status_panel())
        center.addWidget(self._build_gate_panel())
        center.addWidget(self._build_rounds_panel())
        layout.addLayout(center, 2)

        right = QVBoxLayout()
        right.setSpacing(12)
        right.addWidget(self._build_plan_panel())
        right.addWidget(self._build_health_panel())
        right.addWidget(self._build_log_panel(), 1)
        layout.addLayout(right, 1)
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
        body.addWidget(self._button("启动轻量控制", "success", self.controller.start_clicked))
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
        control_grid.addWidget(self._button("批量进房", "", self._on_enter_room), 1, 0)
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
        panel, body = self._panel("账号状态", "等待同房")
        grid = QGridLayout()
        grid.setSpacing(10)
        for index, account_id in enumerate(ACCOUNT_IDS):
            grid.addWidget(self._build_account_card(account_id), index // 2, index % 2)
        body.addLayout(grid)
        return panel

    def _build_gate_panel(self) -> QWidget:
        panel, body = self._panel("发号门槛", "待确认")
        gate_grid = QGridLayout()
        gate_grid.setSpacing(8)
        gates = [
            ("同房", "等待 a1/a2/a3/a4"),
            ("同局", "等待共同局号"),
            ("倒计时", "门槛 >= 10 秒"),
            ("预检", "发号前并行确认"),
        ]
        for index, (name, detail) in enumerate(gates):
            gate_grid.addWidget(self._gate_item(name, "待确认", detail), index // 2, index % 2)
        body.addLayout(gate_grid)
        return panel

    def _build_rounds_panel(self) -> QWidget:
        panel, body = self._panel("执行轮次", "0/10")
        self.round_table = self._table(
            ["轮次", "倒计时", "间隔", "a1", "a2", "a3", "a4", "耗时", "缺口"],
            3,
        )
        body.addWidget(self.round_table)
        self._seed_round_table()
        return panel

    def _build_plan_panel(self) -> QWidget:
        panel, body = self._panel("本轮计划", "对冲")
        for account_id in ACCOUNT_IDS:
            row = QFrame()
            row.setObjectName("planLine")
            layout = QHBoxLayout(row)
            layout.setContentsMargins(8, 8, 8, 8)
            layout.setSpacing(10)
            row.setMinimumHeight(66)
            amount = QLabel("-")
            amount.setObjectName("planAmount")
            amount.setAlignment(Qt.AlignmentFlag.AlignCenter)
            amount.setFixedWidth(78)
            side = QLabel(f"{account_id} · 等待")
            side.setObjectName("planTitle")
            chips = QLabel("未计算")
            chips.setObjectName("planMeta")
            chips.setWordWrap(True)
            role = self._pill("副", "")
            layout.addWidget(amount)
            text_box = QVBoxLayout()
            text_box.setSpacing(3)
            text_box.addWidget(side)
            text_box.addWidget(chips)
            layout.addLayout(text_box, 1)
            layout.addWidget(role)
            body.addWidget(row)
            self.plan_rows[account_id] = {"amount": amount, "side": side, "chips": chips, "role": role}
        return panel

    def _build_health_panel(self) -> QWidget:
        panel, body = self._panel("运行摘要", "正常")
        grid = QGridLayout()
        grid.setSpacing(8)
        for index, (value, label) in enumerate([
            ("0", "测试轮次"),
            ("0", "总缺口"),
            ("-", "最慢整轮"),
            ("-", "最慢点击"),
        ]):
            grid.addWidget(self._health_box(value, label), index // 2, index % 2)
        body.addLayout(grid)
        return panel

    def _build_log_panel(self) -> QWidget:
        panel, body = self._panel("轻量日志", "摘要")
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setObjectName("logView")
        self.log_view.setMinimumHeight(220)
        body.addWidget(self.log_view)
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
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

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

        countdown = QLabel("- 秒")
        countdown.setObjectName("countdown")
        layout.addWidget(countdown)
        round_id = QLabel("局号：-")
        balance = QLabel("余额：-")
        state_machine = QLabel("状态机：-")
        pending = QLabel("待确认：-")
        room = QLabel("房间：-")
        for item in (room, round_id, balance, state_machine, pending):
            layout.addWidget(item)

        foot = QHBoxLayout()
        primary = self._button("接管", "", self._protected_notice, enabled=False)
        recapture = self._button("重采", "", self._protected_notice, enabled=False)
        foot.addWidget(primary)
        foot.addWidget(recapture)
        layout.addLayout(foot)
        self.account_cards[account_id] = {
            "role": role,
            "state": state,
            "countdown": countdown,
            "room": room,
            "round": round_id,
            "balance": balance,
            "machine": state_machine,
            "pending": pending,
            "primary": primary,
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
        self.click_interval_input = self._spin(1, 3000, 200)
        self.min_countdown_input = self._spin(0, 300, 10)
        self.confirm_ms_input = self._spin(0, 5000, 1200)
        for widget in [
            self.amount_min_input,
            self.amount_max_input,
            self.click_interval_input,
            self.min_countdown_input,
            self.confirm_ms_input,
        ]:
            widget.valueChanged.connect(self._on_strategy_preview_changed)
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
        layout.addWidget(self._button("应用高级参数", "primary", self._on_apply_advanced))
        return layout

    def _panel(self, title: str, pill_text: str = "") -> tuple[QFrame, QVBoxLayout]:
        panel = QFrame()
        panel.setObjectName("panel")
        outer = QVBoxLayout(panel)
        outer.setContentsMargins(12, 10, 12, 12)
        outer.setSpacing(10)

        head = QHBoxLayout()
        label = QLabel(title)
        label.setObjectName("panelTitle")
        head.addWidget(label)
        head.addStretch()
        if pill_text:
            head.addWidget(self._pill(pill_text, "info"))
        outer.addLayout(head)

        body = QVBoxLayout()
        body.setSpacing(8)
        outer.addLayout(body)
        return panel, body

    def _button(self, text: str, tone: str = "", handler: object | None = None, enabled: bool = True) -> QPushButton:
        button = QPushButton(text)
        button.setProperty("tone", tone)
        button.setEnabled(enabled)
        button.setMinimumHeight(32)
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

    def _health_box(self, value: str, label: str) -> QWidget:
        box = QFrame()
        box.setObjectName("healthBox")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(10, 10, 10, 10)
        number = QLabel(value)
        number.setObjectName("healthValue")
        text = QLabel(label)
        text.setObjectName("hint")
        layout.addWidget(number)
        layout.addWidget(text)
        return box

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
        table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        table.setMinimumHeight(128)
        return table

    def _seed_round_table(self) -> None:
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

    def _on_handoff(self) -> None:
        if self._sync_form_to_controller():
            self.controller.batch_handoff_clicked()

    def _on_enter_room(self) -> None:
        if self._sync_form_to_controller():
            self.controller.batch_enter_room_clicked(self._selected_room_index())

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
                if isinstance(card["primary"], QPushButton):
                    card["primary"].setText("观察" if is_main else "接管")

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

    def _on_account_status_updated(self, payload: object) -> None:
        if not isinstance(payload, list):
            return
        for summary in payload:
            account_id = getattr(summary, "account_id", "")
            card = self.account_cards.get(account_id)
            if not card:
                continue
            card["state"].setText(str(getattr(summary, "state_label", "等待") or "等待"))
            countdown = getattr(summary, "countdown", None)
            card["countdown"].setText(f"{countdown} 秒" if countdown is not None else "- 秒")
            card["room"].setText(f"房间：{getattr(summary, 'room_label', '-') or '-'}")
            card["round"].setText(f"局号：{getattr(summary, 'round_id', '-') or '-'}")
            balance = getattr(summary, "balance", None)
            pending = getattr(summary, "pending_amount", None)
            card["balance"].setText(f"余额：{balance if balance is not None else '-'}")
            card["machine"].setText(f"状态机：{getattr(summary, 'state_machine_label', '-') or '-'}")
            card["pending"].setText(f"待确认：{pending if pending is not None else '-'}")

    def _append_log(self, message: str) -> None:
        self._ui_logs.append(message)
        if len(self._ui_logs) > 200:
            self._ui_logs = self._ui_logs[-200:]
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
            QLabel#countdown {
                font-size: 28px;
                font-weight: 700;
                color: #175cd3;
            }
            QLabel#amount, QLabel#healthValue {
                font-size: 20px;
                font-weight: 700;
                color: #0f172a;
            }
            QLabel#planAmount {
                background: #eef3f8;
                border: 1px solid #e3ebf3;
                border-radius: 7px;
                color: #0f172a;
                font-size: 18px;
                font-weight: 700;
                min-height: 44px;
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
                padding: 6px 12px;
                color: #172033;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #e3edf7;
            }
            QPushButton:pressed {
                background: #cbd8e5;
                border-color: #9fb2c5;
                padding-top: 8px;
                padding-left: 14px;
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
                min-height: 28px;
                padding: 2px 7px;
            }
            QTextEdit#logView, QTableWidget {
                background: #fbfdff;
                border: 1px solid #d9e2ec;
                border-radius: 6px;
            }
            QFrame#summaryCard, QFrame#accountCard, QFrame#gateItem,
            QFrame#planLine, QFrame#healthBox, QFrame#metricRow,
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
