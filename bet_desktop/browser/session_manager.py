"""Managed Playwright sessions for four isolated desktop test instances."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any

from bet_desktop.core.page_state import PageStateSnapshot
from bet_desktop.page.real_detector import RealDetectorConfig, RealPageStateDetector
from bet_desktop.vision.live_game_regions import (
    extract_live_game_text_from_png,
    recognize_live_game_layout_from_png,
)


NETWORK_PROBE_LOG = Path("bet_desktop/artifacts/network_probe_candidates.jsonl")
NETWORK_ENDPOINT_LOG = Path("bet_desktop/artifacts/network_probe_endpoints.jsonl")
NETWORK_SAMPLE_LOG = Path("bet_desktop/artifacts/network_probe_samples.jsonl")
MANUAL_CLICK_PROBE_LOG = Path("bet_desktop/artifacts/manual_click_probe.jsonl")


@dataclass(frozen=True)
class ProxyConfig:
    host: str = ""
    port: str = ""
    username: str = ""
    password: str = ""

    def to_playwright_proxy(self) -> dict[str, str] | None:
        host = self.host.strip()
        port = self.port.strip()
        if not host:
            return None
        server = host if re.match(r"^[a-z]+://", host, re.I) else f"http://{host}"
        if port and ":" not in server.rsplit("/", 1)[-1]:
            server = f"{server}:{port}"
        proxy: dict[str, str] = {"server": server}
        if self.username:
            proxy["username"] = self.username
        if self.password:
            proxy["password"] = self.password
        return proxy


@dataclass(frozen=True)
class AccountConfig:
    username: str = ""
    password: str = ""
    cookie_or_token: str = ""
    note: str = ""


@dataclass(frozen=True)
class BrowserInstanceConfig:
    instance_id: str
    login_url: str
    target_url: str = ""
    proxy: ProxyConfig = field(default_factory=ProxyConfig)
    account: AccountConfig = field(default_factory=AccountConfig)
    headless: bool = False


@dataclass(frozen=True)
class BrowserSnapshot:
    instance_id: str
    status: str
    message: str = ""
    url: str = ""
    batch_id: str = "-"
    countdown_seconds: int = 0
    balance_text: str = "-"
    screenshot_png: bytes | None = None
    proxy_state: str = "未检测"
    fingerprint_state: str = "独立"
    game_detected: bool = False
    page_stage: str = "unknown"


class BrowserSessionError(RuntimeError):
    pass


class BrowserSessionManager:
    """Launches one isolated Chromium browser per test instance."""

    def __init__(self, *, detector: RealPageStateDetector | None = None) -> None:
        self.detector = detector or RealPageStateDetector(
            config=RealDetectorConfig(enforce_safe_countdown=False)
        )
        self._playwright = None
        self._sessions: dict[str, dict[str, Any]] = {}

    async def start(self) -> None:
        if self._playwright is None:
            from playwright.async_api import async_playwright

            self._playwright = await async_playwright().start()

    async def stop(self) -> None:
        for session in list(self._sessions.values()):
            browser = session.get("browser")
            if browser is not None:
                await browser.close()
        self._sessions.clear()
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None

    async def launch_instances(self, configs: list[BrowserInstanceConfig]) -> list[BrowserSnapshot]:
        await self.start()
        return await asyncio.gather(*(self.launch_instance(config) for config in configs))

    async def launch_instance(self, config: BrowserInstanceConfig) -> BrowserSnapshot:
        if self._playwright is None:
            await self.start()
        await self.close_instance(config.instance_id)

        launch_options: dict[str, Any] = {"headless": config.headless}
        proxy = config.proxy.to_playwright_proxy()
        if proxy:
            launch_options["proxy"] = proxy

        browser = await self._playwright.chromium.launch(**launch_options)
        context = await browser.new_context(
            viewport={"width": 960, "height": 620},
            device_scale_factor=1,
            ignore_https_errors=True,
        )
        page = await context.new_page()
        state_cache: dict[str, Any] = {}
        self._sessions[config.instance_id] = {
            "browser": browser,
            "context": context,
            "page": page,
            "config": config,
            "state_cache": state_cache,
        }
        self._attach_runtime_state_collectors(config.instance_id, page, state_cache)
        await self._install_manual_click_probe(config.instance_id, page)
        if config.login_url:
            await page.goto(config.login_url, wait_until="domcontentloaded", timeout=45000)
        return await self.snapshot(config.instance_id, status="已打开登录页", message="浏览器实例已启动")

    async def close_instance(self, instance_id: str) -> None:
        session = self._sessions.pop(instance_id, None)
        if not session:
            return
        browser = session.get("browser")
        if browser is not None:
            await browser.close()

    async def close_all_instances(self) -> None:
        for instance_id in list(self._sessions):
            await self.close_instance(instance_id)

    async def fill_login_forms(self, instance_ids: list[str] | None = None) -> list[BrowserSnapshot]:
        selected_ids = instance_ids or list(self._sessions)
        active_ids = [instance_id for instance_id in selected_ids if instance_id in self._sessions]
        if not active_ids:
            return []
        return await asyncio.gather(*(self.fill_login_form(instance_id) for instance_id in active_ids))

    async def fill_login_form(self, instance_id: str) -> BrowserSnapshot:
        session = self._require_session(instance_id)
        page = session["page"]
        config: BrowserInstanceConfig = session["config"]
        account = config.account
        if not account.username or not account.password:
            return await self.snapshot(instance_id, status="登录资料缺失", message="账号或密码为空")

        await self._ensure_login_form_visible(page)
        await self._switch_login_modal_to_login_tab(page)
        user_locator, password_locator = await self._login_input_locators(page)
        await self._type_login_value(user_locator, account.username, field_name="账号")
        await self._type_login_value(password_locator, account.password, field_name="密码")
        await self._click_login_submit(page, password_locator)
        await page.wait_for_timeout(1000)
        return await self.snapshot(instance_id, status="等待人工验证码", message="已提交登录表单，请人工处理验证码")

    async def open_targets(self) -> list[BrowserSnapshot]:
        return await asyncio.gather(*(self.open_target(instance_id) for instance_id in self._sessions))

    async def open_target(self, instance_id: str) -> BrowserSnapshot:
        session = self._require_session(instance_id)
        page = session["page"]
        config: BrowserInstanceConfig = session["config"]
        if not config.target_url:
            return await self.snapshot(instance_id, status="目标页缺失", message="未配置目标页面")
        await page.goto(config.target_url, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(800)
        return await self.snapshot(instance_id, status="目标页就绪", message="已进入目标页面")

    async def snapshot_all(self) -> list[BrowserSnapshot]:
        if not self._sessions:
            return []
        return await asyncio.gather(*(self.snapshot(instance_id) for instance_id in self._sessions))

    async def snapshot(self, instance_id: str, *, status: str = "运行中", message: str = "") -> BrowserSnapshot:
        session = self._sessions.get(instance_id)
        if session is None:
            return BrowserSnapshot(instance_id=instance_id, status="未启动", message="浏览器实例未启动")
        page = session["page"]
        config: BrowserInstanceConfig = session["config"]
        state_cache: dict[str, Any] = session.get("state_cache", {})
        screenshot = await page.screenshot(type="png", full_page=False)
        visual_state = recognize_live_game_layout_from_png(screenshot)
        text_state = extract_live_game_text_from_png(screenshot) if visual_state.game_visible else None
        page_stage = self._page_stage(page.url, visual_state.game_visible)
        batch_id = "等待进入游戏页" if page_stage != "game" else "未识别"
        countdown = -1
        balance_text = "等待进入游戏页" if page_stage != "game" else "未识别"
        phase = str(state_cache.get("phase") or "") if page_stage == "game" else ""
        if status == "运行中":
            if page_stage == "game" and phase:
                status = phase
            elif page_stage == "game":
                status = "游戏页已识别"
            elif page_stage == "embedded_pending":
                status = "目标页等待游戏画面"
            elif "botion" in page.url.lower():
                status = "等待人工验证码"
        try:
            if page_stage == "game":
                state = await self.detector(page)
                if self._is_trusted_batch_id(state.batch_id):
                    batch_id = state.batch_id
                if state.countdown_seconds > 0:
                    countdown = state.countdown_seconds
        except Exception:
            state = PageStateSnapshot(is_processing=False, countdown_seconds=-1, batch_id="未识别")

        if page_stage == "game":
            balance_text = await self._read_balance_text(page)
            if text_state is not None:
                if self._is_trusted_batch_id(text_state.batch_id):
                    batch_id = text_state.batch_id
                if text_state.countdown_seconds >= 0:
                    countdown = text_state.countdown_seconds
                if text_state.balance_text:
                    balance_text = text_state.balance_text
                elif not text_state.ocr_available:
                    balance_text = "未识别（缺少OCR引擎）"
        proxy_state = await self._check_proxy_state(page, config.proxy)
        fingerprint_state = await self._fingerprint_state(page)
        if page_stage == "game" and visual_state.game_visible and balance_text == "未识别":
            balance_text = "未识别（余额区已定位）"
        return BrowserSnapshot(
            instance_id=instance_id,
            status=status,
            message=message,
            url=page.url,
            batch_id=batch_id,
            countdown_seconds=countdown,
            balance_text=balance_text,
            screenshot_png=screenshot,
            proxy_state=proxy_state,
            fingerprint_state=fingerprint_state,
            game_detected=(page_stage == "game"),
            page_stage=page_stage,
        )

    def page(self, instance_id: str) -> Any | None:
        session = self._sessions.get(instance_id)
        return session.get("page") if session else None

    def pages(self) -> dict[str, Any]:
        return {instance_id: session["page"] for instance_id, session in self._sessions.items()}

    def _require_session(self, instance_id: str) -> dict[str, Any]:
        session = self._sessions.get(instance_id)
        if session is None:
            raise BrowserSessionError(f"browser instance {instance_id} is not launched")
        return session

    @staticmethod
    def _is_trusted_batch_id(value: str) -> bool:
        text = str(value or "").strip()
        return len(text) >= 10 and "-" in text and bool(re.search(r"\d", text))

    def _attach_runtime_state_collectors(self, instance_id: str, page: Any, state_cache: dict[str, Any]) -> None:
        def on_request(request: Any) -> None:
            self._record_request_event(instance_id, request)

        def on_response(response: Any) -> None:
            asyncio.create_task(self._record_response_state(instance_id, response, state_cache))

        def on_websocket(ws: Any) -> None:
            ws_url = str(getattr(ws, "url", "websocket"))
            self._write_network_endpoint_event(
                instance_id=instance_id,
                transport="websocket",
                source_url=ws_url,
                event="open",
            )
            ws.on(
                "framereceived",
                lambda frame: self._record_text_state(
                    state_cache,
                    frame,
                    instance_id=instance_id,
                    source_url=ws_url,
                    transport="websocket",
                    direction="inbound",
                ),
            )
            ws.on(
                "framesent",
                lambda frame: self._record_outbound_text(
                    frame,
                    instance_id=instance_id,
                    source_url=ws_url,
                    transport="websocket",
                ),
            )

        page.on("request", on_request)
        page.on("response", on_response)
        page.on("websocket", on_websocket)

    async def _install_manual_click_probe(self, instance_id: str, page: Any) -> None:
        async def on_manual_click(source: Any, payload: Any) -> None:
            self._write_manual_click_probe_event(instance_id, source, payload)

        try:
            await page.expose_binding("__betDesktopManualClickProbe", on_manual_click)
            await page.add_init_script(
                """
                (() => {
                  if (window.__betDesktopManualClickProbeInstalled) return;
                  window.__betDesktopManualClickProbeInstalled = true;
                  window.addEventListener("pointerdown", event => {
                    try {
                      window.__betDesktopManualClickProbe({
                        page_ts_ms: Date.now(),
                        perf_ts_ms: Math.round(performance.now()),
                        x: Math.round(event.clientX),
                        y: Math.round(event.clientY),
                        button: event.button,
                        buttons: event.buttons,
                        pointer_type: event.pointerType || "",
                        target_tag: event.target && event.target.tagName ? event.target.tagName : "",
                        href: window.location.href
                      });
                    } catch (_) {}
                  }, true);
                })();
                """
            )
            await page.evaluate(
                """
                (() => {
                  if (window.__betDesktopManualClickProbeInstalled) return;
                  window.__betDesktopManualClickProbeInstalled = true;
                  window.addEventListener("pointerdown", event => {
                    try {
                      window.__betDesktopManualClickProbe({
                        page_ts_ms: Date.now(),
                        perf_ts_ms: Math.round(performance.now()),
                        x: Math.round(event.clientX),
                        y: Math.round(event.clientY),
                        button: event.button,
                        buttons: event.buttons,
                        pointer_type: event.pointerType || "",
                        target_tag: event.target && event.target.tagName ? event.target.tagName : "",
                        href: window.location.href
                      });
                    } catch (_) {}
                  }, true);
                })();
                """
            )
        except Exception:
            return

    @classmethod
    def _record_request_event(cls, instance_id: str, request: Any) -> None:
        try:
            url = str(request.url)
            if cls._looks_like_static_asset(url):
                return
            post_data = getattr(request, "post_data", None) or ""
            cls._write_network_endpoint_event(
                instance_id=instance_id,
                transport="http",
                source_url=url,
                event="request",
                direction="outbound",
                method=str(getattr(request, "method", "")),
                resource_type=str(getattr(request, "resource_type", "")),
                size=len(post_data),
                keyword_hits=cls._keyword_hits(f"{url} {post_data[:2000]}"),
                payload_hash=cls._payload_hash(post_data) if post_data else "",
            )
            cls._write_network_payload_sample(
                instance_id=instance_id,
                transport="http",
                source_url=url,
                payload=post_data,
                reason="targeted_http_request",
            )
        except Exception:
            return

    async def _record_response_state(self, instance_id: str, response: Any, state_cache: dict[str, Any]) -> None:
        try:
            url = str(response.url)
            if not self._looks_like_static_asset(url):
                self._write_network_endpoint_event(
                    instance_id=instance_id,
                    transport="http",
                    source_url=url,
                    event="response",
                    direction="inbound",
                    status=getattr(response, "status", None),
                    content_type=str(response.headers.get("content-type", ""))[:120],
                    method=str(getattr(response.request, "method", "")),
                    resource_type=str(getattr(response.request, "resource_type", "")),
                )
            if not self._looks_like_state_url(url):
                return
            content_type = str(response.headers.get("content-type", "")).lower()
            if "json" in content_type:
                payload = await response.json()
                self._record_payload_state(
                    state_cache,
                    payload,
                    instance_id=instance_id,
                    source_url=url,
                    transport="http",
                )
                return
            text = await response.text()
            self._write_network_payload_sample(
                instance_id=instance_id,
                transport="http",
                source_url=url,
                payload=text,
                reason="targeted_http_response",
            )
            self._record_text_state(
                state_cache,
                text,
                instance_id=instance_id,
                source_url=url,
                transport="http",
            )
        except Exception:
            return

    @staticmethod
    def _looks_like_state_url(url: str) -> bool:
        lowered = url.lower()
        if BrowserSessionManager._looks_like_static_asset(lowered):
            return False
        return any(keyword in lowered for keyword in ("api", "game", "member", "balance", "wallet", "round", "table", "hall"))

    @staticmethod
    def _looks_like_static_asset(url: str) -> bool:
        lowered = url.lower().split("?", 1)[0]
        skip_suffixes = (
            ".png",
            ".jpg",
            ".jpeg",
            ".webp",
            ".gif",
            ".svg",
            ".css",
            ".js",
            ".map",
            ".woff",
            ".woff2",
            ".ttf",
            ".otf",
            ".avif",
            ".mp3",
            ".mp4",
        )
        return lowered.endswith(skip_suffixes)

    @classmethod
    def _record_text_state(
        cls,
        state_cache: dict[str, Any],
        payload: Any,
        *,
        instance_id: str = "",
        source_url: str = "",
        transport: str = "unknown",
        direction: str = "inbound",
    ) -> None:
        if isinstance(payload, bytes):
            try:
                payload = payload.decode("utf-8", errors="ignore")
            except Exception:
                cls._write_network_endpoint_event(
                    instance_id=instance_id,
                    transport=transport,
                    source_url=source_url,
                    event="binary_frame",
                    direction=direction,
                    size=len(payload),
                    payload_hash=cls._payload_hash(payload),
                )
                return
        if not isinstance(payload, str) or not payload.strip():
            return
        cls._write_network_endpoint_event(
            instance_id=instance_id,
            transport=transport,
            source_url=source_url,
            event="frame",
            direction=direction,
            size=len(payload),
            keyword_hits=cls._keyword_hits(payload),
            payload_hash=cls._payload_hash(payload),
        )
        cls._write_network_payload_sample(
            instance_id=instance_id,
            transport=transport,
            source_url=source_url,
            payload=payload,
            reason="targeted_ws_frame",
        )
        text = payload[:120000]
        try:
            cls._record_payload_state(
                state_cache,
                json.loads(text),
                instance_id=instance_id,
                source_url=source_url,
                transport=transport,
            )
        except Exception:
            pass
        cls._record_regex_state(state_cache, text)
        regex_found: dict[str, Any] = {}
        cls._record_regex_state(regex_found, text)
        cls._write_network_probe_candidate(
            instance_id=instance_id,
            transport=transport,
            source_url=source_url,
            fields=[
                {"kind": key, "path": "$regex", "value": str(value)[:80]}
                for key, value in regex_found.items()
                if value not in (None, "", -1)
            ],
        )

    @classmethod
    def _record_outbound_text(
        cls,
        payload: Any,
        *,
        instance_id: str = "",
        source_url: str = "",
        transport: str = "websocket",
    ) -> None:
        if isinstance(payload, bytes):
            try:
                payload_text = payload.decode("utf-8", errors="ignore")
            except Exception:
                cls._write_network_endpoint_event(
                    instance_id=instance_id,
                    transport=transport,
                    source_url=source_url,
                    event="binary_frame",
                    direction="outbound",
                    size=len(payload),
                    payload_hash=cls._payload_hash(payload),
                )
                return
        else:
            payload_text = str(payload or "")
        if not payload_text:
            return
        cls._write_network_endpoint_event(
            instance_id=instance_id,
            transport=transport,
            source_url=source_url,
            event="frame",
            direction="outbound",
            size=len(payload_text),
            keyword_hits=cls._keyword_hits(payload_text),
            payload_hash=cls._payload_hash(payload_text),
        )
        cls._write_network_payload_sample(
            instance_id=instance_id,
            transport=transport,
            source_url=source_url,
            payload=payload_text,
            reason="targeted_ws_frame_sent",
        )

    @classmethod
    def _record_payload_state(
        cls,
        state_cache: dict[str, Any],
        payload: Any,
        *,
        instance_id: str = "",
        source_url: str = "",
        transport: str = "unknown",
    ) -> None:
        found = cls._extract_runtime_fields(payload)
        for key, value in found.items():
            if value not in (None, "", -1):
                state_cache[key] = value
        cls._write_network_probe_candidate(
            instance_id=instance_id,
            transport=transport,
            source_url=source_url,
            fields=cls._extract_runtime_field_paths(payload),
        )

    @classmethod
    def _extract_runtime_fields(cls, payload: Any) -> dict[str, Any]:
        found: dict[str, Any] = {}
        batch_keys = {"batch_id", "batchid", "batch", "issue", "issueno", "roundid", "roundno", "gameid", "shoeid"}
        countdown_keys = {"countdown", "countdownseconds", "leftseconds", "timeleft", "remaintime", "betseconds"}
        balance_keys = {"balance", "availablebalance", "wallet", "credit", "money", "cash", "amount"}
        phase_keys = {"phase", "status", "state", "gamestatus", "roundstatus", "stage"}

        def visit(value: Any, depth: int = 0) -> None:
            if depth > 7:
                return
            if isinstance(value, dict):
                for raw_key, child in value.items():
                    key = re.sub(r"[^a-z0-9]", "", str(raw_key).lower())
                    if key in batch_keys and cls._is_batch_like(child):
                        found.setdefault("batch_id", str(child))
                    elif key in countdown_keys and cls._to_int(child) is not None:
                        found.setdefault("countdown", cls._to_int(child))
                    elif key in balance_keys and cls._is_balance_like(child):
                        found.setdefault("balance", str(child))
                    elif key in phase_keys and cls._is_phase_like(child):
                        found.setdefault("phase", str(child))
                    visit(child, depth + 1)
            elif isinstance(value, list):
                for child in value[:80]:
                    visit(child, depth + 1)
            elif isinstance(value, str):
                cls._record_regex_state(found, value)

        visit(payload)
        return found

    @classmethod
    def _extract_runtime_field_paths(cls, payload: Any) -> list[dict[str, str]]:
        candidates: list[dict[str, str]] = []
        key_map = {
            "batch": {"batch_id", "batchid", "batch", "issue", "issueno", "roundid", "roundno", "gameid", "shoeid"},
            "countdown": {"countdown", "countdownseconds", "leftseconds", "timeleft", "remaintime", "betseconds"},
            "balance": {"balance", "availablebalance", "wallet", "credit", "money", "cash"},
            "phase": {"phase", "status", "state", "gamestatus", "roundstatus", "stage"},
        }

        def classify(raw_key: Any, child: Any) -> str:
            key = re.sub(r"[^a-z0-9]", "", str(raw_key).lower())
            if key in key_map["batch"] and cls._is_batch_like(child):
                return "batch"
            if key in key_map["countdown"] and cls._to_int(child) is not None:
                return "countdown"
            if key in key_map["balance"] and cls._is_balance_like(child):
                return "balance"
            if key in key_map["phase"] and cls._is_phase_like(child):
                return "phase"
            return ""

        def visit(value: Any, path: str, depth: int = 0) -> None:
            if depth > 7 or len(candidates) >= 80:
                return
            if isinstance(value, dict):
                for raw_key, child in value.items():
                    child_path = f"{path}.{raw_key}"
                    kind = classify(raw_key, child)
                    if kind:
                        candidates.append(
                            {
                                "kind": kind,
                                "path": child_path,
                                "value": str(child)[:80],
                            }
                        )
                    visit(child, child_path, depth + 1)
            elif isinstance(value, list):
                for index, child in enumerate(value[:80]):
                    visit(child, f"{path}[{index}]", depth + 1)

        visit(payload, "$")
        return candidates

    @staticmethod
    def _write_network_probe_candidate(
        *,
        instance_id: str,
        transport: str,
        source_url: str,
        fields: list[dict[str, str]],
    ) -> None:
        if not fields:
            return
        try:
            NETWORK_PROBE_LOG.parent.mkdir(parents=True, exist_ok=True)
            record = {
                "ts": datetime.now().isoformat(timespec="seconds"),
                "instance_id": instance_id,
                "transport": transport,
                "url": BrowserSessionManager._sanitize_probe_url(source_url),
                "fields": fields[:20],
            }
            with NETWORK_PROBE_LOG.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            return

    @staticmethod
    def _write_network_endpoint_event(
        *,
        instance_id: str,
        transport: str,
        source_url: str,
        event: str,
        **extra: Any,
    ) -> None:
        try:
            NETWORK_ENDPOINT_LOG.parent.mkdir(parents=True, exist_ok=True)
            record = {
                "ts": datetime.now().isoformat(timespec="seconds"),
                "instance_id": instance_id,
                "transport": transport,
                "event": event,
                "url": BrowserSessionManager._sanitize_probe_url(source_url),
            }
            record.update({key: value for key, value in extra.items() if value not in (None, "", [])})
            with NETWORK_ENDPOINT_LOG.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            return

    @staticmethod
    def _write_manual_click_probe_event(instance_id: str, source: Any, payload: Any) -> None:
        try:
            if not isinstance(payload, dict):
                payload = {}
            source_page = getattr(source, "get", lambda _key, _default=None: _default)("page", None)
            source_url = str(getattr(source_page, "url", "") or payload.get("href") or "")
            record = {
                "ts": datetime.now().isoformat(timespec="milliseconds"),
                "instance_id": instance_id,
                "event": "manual_pointerdown",
                "x": int(payload.get("x", -1)),
                "y": int(payload.get("y", -1)),
                "button": int(payload.get("button", -1)),
                "buttons": int(payload.get("buttons", 0)),
                "pointer_type": str(payload.get("pointer_type", ""))[:32],
                "target_tag": str(payload.get("target_tag", ""))[:32],
                "page_ts_ms": int(payload.get("page_ts_ms", 0)),
                "perf_ts_ms": int(payload.get("perf_ts_ms", 0)),
                "url": BrowserSessionManager._sanitize_probe_url(source_url),
            }
            MANUAL_CLICK_PROBE_LOG.parent.mkdir(parents=True, exist_ok=True)
            with MANUAL_CLICK_PROBE_LOG.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            return

    @staticmethod
    def _payload_hash(payload: Any) -> str:
        if isinstance(payload, bytes):
            data = payload
        else:
            data = str(payload or "").encode("utf-8", errors="ignore")
        if not data:
            return ""
        return hashlib.sha256(data).hexdigest()[:16]

    @staticmethod
    def _write_network_payload_sample(
        *,
        instance_id: str,
        transport: str,
        source_url: str,
        payload: str,
        reason: str,
    ) -> None:
        if not BrowserSessionManager._should_sample_payload(source_url, payload):
            return
        try:
            NETWORK_SAMPLE_LOG.parent.mkdir(parents=True, exist_ok=True)
            record = {
                "ts": datetime.now().isoformat(timespec="seconds"),
                "instance_id": instance_id,
                "transport": transport,
                "reason": reason,
                "url": BrowserSessionManager._sanitize_probe_url(source_url),
                "size": len(payload),
                "summary": BrowserSessionManager._payload_summary(payload),
            }
            with NETWORK_SAMPLE_LOG.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            return

    @staticmethod
    def _should_sample_payload(source_url: str, payload: str) -> bool:
        lowered_url = str(source_url).lower()
        lowered_payload = payload[:2000].lower()
        if "getusernowalletinfo" in lowered_url:
            return True
        if "wss-g." in lowered_url and len(payload) not in (3, 5, 10, 12):
            return True
        return any(
            keyword in lowered_payload
            for keyword in ("balance", "wallet", "credit", "countdown", "leftseconds", "timeleft", "牌局", "倒计时", "余额")
        )

    @staticmethod
    def _payload_summary(payload: str) -> dict[str, Any]:
        text = payload[:8000]
        try:
            parsed = json.loads(text)
        except Exception:
            numbers = re.findall(r"\d+(?:\.\d{1,2})?", text)
            return {
                "format": "text",
                "prefix": BrowserSessionManager._redact_probe_text(text[:240]),
                "number_samples": numbers[:30],
                "keyword_hits": BrowserSessionManager._keyword_hits(text),
            }
        paths: list[dict[str, str]] = []

        def visit(value: Any, path: str, depth: int = 0) -> None:
            if depth > 5 or len(paths) >= 80:
                return
            if isinstance(value, dict):
                for key, child in value.items():
                    child_path = f"{path}.{key}"
                    key_text = str(key).lower()
                    interesting_key = any(
                        name in key_text
                        for name in ("balance", "wallet", "credit", "money", "count", "time", "round", "issue", "status")
                    )
                    if interesting_key or isinstance(child, (int, float)):
                        paths.append({"path": child_path, "value": BrowserSessionManager._redact_probe_text(str(child)[:120])})
                    visit(child, child_path, depth + 1)
            elif isinstance(value, list):
                for index, child in enumerate(value[:30]):
                    visit(child, f"{path}[{index}]", depth + 1)

        visit(parsed, "$")
        return {"format": "json", "paths": paths}

    @staticmethod
    def _sanitize_probe_url(url: str) -> str:
        text = str(url or "")
        text = re.sub(
            r"([?&](?:token|sign|auth|authStr|password|pwd|session|cookie|key|account|extraParam)=)[^&]+",
            r"\1***",
            text,
            flags=re.I,
        )
        return text[:500]

    @staticmethod
    def _redact_probe_text(text: str) -> str:
        redacted = str(text)
        redacted = re.sub(r"(token|authStr|authorization|password|session|cookie)[\"'=:\s]+[^,;&\s\"']+", r"\1=***", redacted, flags=re.I)
        redacted = re.sub(r"121310_[0-9A-Za-z_\-]+", "account=***", redacted)
        return redacted

    @staticmethod
    def _keyword_hits(text: str) -> list[str]:
        lowered = text.lower()
        keywords = (
            "balance",
            "wallet",
            "credit",
            "money",
            "countdown",
            "timeleft",
            "leftseconds",
            "remain",
            "round",
            "issue",
            "batch",
            "status",
            "stage",
            "余额",
            "倒计时",
            "剩余",
            "局号",
            "牌局",
        )
        return [keyword for keyword in keywords if keyword in lowered][:12]

    @staticmethod
    def _record_regex_state(state_cache: dict[str, Any], text: str) -> None:
        patterns = {
            "batch_id": (
                r"(?:牌局编号|局号|期号|batch(?:_id)?|round(?:_id)?|issue(?:No)?)[\"'：:=\s#-]+([0-9A-Za-z\-]{4,})",
            ),
            "countdown": (
                r"(?:倒计时|剩余时间|countdown|timeLeft|leftSeconds)[\"'：:=\s]+(\d{1,2})",
            ),
            "balance": (
                r"(?:余额|balance|availableBalance|wallet|credit)[\"'：:=\s]+([0-9]+(?:\.[0-9]{1,2})?)",
            ),
            "phase": (
                r"(下注中|投注中|准备中|发牌中|开牌中|派彩中|封盘|betting|processing|closed)",
            ),
        }
        for key, regexes in patterns.items():
            if state_cache.get(key) not in (None, "", -1):
                continue
            for pattern in regexes:
                match = re.search(pattern, text, flags=re.I)
                if not match:
                    continue
                value: Any = match.group(1)
                if key == "countdown":
                    value = int(value)
                state_cache[key] = value
                break

    @staticmethod
    def _is_batch_like(value: Any) -> bool:
        text = str(value)
        return bool(re.search(r"[0-9A-Za-z]", text)) and 3 <= len(text) <= 80

    @staticmethod
    def _to_int(value: Any) -> int | None:
        try:
            number = int(float(str(value)))
        except Exception:
            return None
        return number if 0 <= number <= 120 else None

    @staticmethod
    def _is_balance_like(value: Any) -> bool:
        try:
            number = float(str(value))
        except Exception:
            return False
        return 0 <= number <= 1_000_000

    @staticmethod
    def _is_phase_like(value: Any) -> bool:
        text = str(value)
        if not text or len(text) > 60:
            return False
        return bool(re.search(r"下注|投注|准备|发牌|开牌|派彩|封盘|bet|process|close|ready|wait", text, re.I))

    @staticmethod
    async def _ensure_login_form_visible(page: Any) -> None:
        if await page.locator("input").count() > 0:
            return
        try:
            await page.locator("text=登录").first.click(timeout=3000)
            await page.wait_for_timeout(800)
            if await page.locator("input").count() > 0:
                return
        except Exception:
            pass
        viewport = page.viewport_size or {"width": 960, "height": 620}
        width = float(viewport.get("width", 960))
        height = float(viewport.get("height", 620))
        click_points = (
            (width / 2 + 78, height - 52),
            (width / 2 + 108, 24),
        )
        for x, y in click_points:
            await page.mouse.click(x, y)
            await page.wait_for_timeout(800)
            if await page.locator("input").count() > 0:
                return
        await page.wait_for_timeout(1500)

    @staticmethod
    async def _switch_login_modal_to_login_tab(page: Any) -> None:
        try:
            await page.locator("text=登录").last.click(timeout=1500)
            await page.wait_for_timeout(500)
            return
        except Exception:
            pass
        first_input = page.locator("input").first
        try:
            box = await first_input.bounding_box(timeout=2000)
        except Exception:
            box = None
        if box is not None:
            await page.mouse.click(box["x"] + box["width"] * 0.72, max(10, box["y"] - 44))
            await page.wait_for_timeout(500)

    @staticmethod
    async def _login_input_locators(page: Any) -> tuple[Any, Any]:
        user_locator = page.locator(
            'input[name*="user" i], input[id*="user" i], input[placeholder*="账号"], '
            'input[placeholder*="用户名"], input[placeholder*="手机"], input[placeholder*="手機"]'
        ).first
        password_locator = page.locator(
            'input[type=password], input[name*="pass" i], input[id*="pass" i], '
            'input[placeholder*="登录密码"], input[placeholder*="密碼"], input[placeholder*="密码"]'
        ).first
        try:
            await user_locator.wait_for(state="visible", timeout=2500)
            await password_locator.wait_for(state="visible", timeout=2500)
            return user_locator, password_locator
        except Exception:
            visible_inputs = page.locator("input:visible")
            count = await visible_inputs.count()
            if count >= 2:
                return visible_inputs.nth(0), visible_inputs.nth(1)
            raise BrowserSessionError("未能识别登录框中的账号与密码输入框")

    @staticmethod
    async def _type_login_value(locator: Any, value: str, *, field_name: str) -> None:
        await locator.click(timeout=8000)
        await locator.press("Control+A", timeout=3000)
        await locator.press("Backspace", timeout=3000)
        await locator.type(value, delay=35, timeout=12000)
        try:
            current_value = await locator.input_value(timeout=3000)
        except Exception:
            current_value = ""
        if current_value != value:
            await locator.fill(value, timeout=5000)
            current_value = await locator.input_value(timeout=3000)
        if current_value != value:
            raise BrowserSessionError(f"{field_name}输入校验失败")

    @staticmethod
    async def _click_login_submit(page: Any, password_locator: Any) -> None:
        password_box = await password_locator.bounding_box(timeout=3000)
        if password_box is None:
            raise BrowserSessionError("未能定位密码框，无法提交登录")

        candidates = page.locator(
            "button:has-text('登录'), button:has-text('登入'), button:has-text('Login'), "
            "[role=button]:has-text('登录'), [role=button]:has-text('登入'), [role=button]:has-text('Login'), "
            "a:has-text('登录'), a:has-text('登入'), div:has-text('登录'), div:has-text('登入')"
        )
        count = min(await candidates.count(), 30)
        password_bottom = password_box["y"] + password_box["height"]
        for index in range(count):
            candidate = candidates.nth(index)
            try:
                box = await candidate.bounding_box(timeout=500)
            except Exception:
                box = None
            if box is None:
                continue
            is_below_password = box["y"] >= password_bottom + 12
            is_button_sized = 70 <= box["width"] <= 360 and 24 <= box["height"] <= 80
            if is_below_password and is_button_sized:
                try:
                    await candidate.click(timeout=1500)
                    return
                except Exception:
                    continue

        await page.mouse.click(
            password_box["x"] + password_box["width"] / 2,
            password_bottom + 58,
        )

    @staticmethod
    async def _check_proxy_state(page: Any, proxy: ProxyConfig) -> str:
        label = "已配置代理" if proxy.host.strip() else "未配置代理"
        try:
            result = await page.evaluate(
                """async () => {
                    const start = performance.now();
                    try {
                        const url = new URL("/favicon.ico", location.origin);
                        url.searchParams.set("__bd_probe", String(Date.now()));
                        const response = await fetch(url.toString(), {
                            method: "GET",
                            cache: "no-store",
                            credentials: "include",
                        });
                        return {
                            ok: response.ok,
                            status: response.status,
                            latency: Math.round(performance.now() - start),
                        };
                    } catch (error) {
                        return {
                            ok: false,
                            status: 0,
                            latency: Math.round(performance.now() - start),
                            error: String(error && error.message || error),
                        };
                    }
                }"""
            )
            latency = int(result.get("latency", 0)) if isinstance(result, dict) else 0
            ok = bool(result.get("ok")) if isinstance(result, dict) else False
            status = int(result.get("status", 0)) if isinstance(result, dict) else 0
            if status > 0:
                return f"{label} · 网络可达 · HTTP {status} · {latency}毫秒"
            return f"{label} · 探测异常 · HTTP - · {latency}毫秒"
        except Exception as exc:
            return f"{label} · 探测失败 · {type(exc).__name__}"

    @staticmethod
    async def _fingerprint_state(page: Any) -> str:
        try:
            payload = await page.evaluate(
                """() => ({
                    userAgent: navigator.userAgent || "",
                    language: navigator.language || "",
                    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "",
                    dpr: window.devicePixelRatio || 1,
                    viewport: `${window.innerWidth}x${window.innerHeight}`,
                    platform: navigator.platform || "",
                })"""
            )
            text = "|".join(str(payload.get(key, "")) for key in sorted(payload)) if isinstance(payload, dict) else ""
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8] if text else "unknown"
            return f"独立上下文 · 指纹摘要 {digest}"
        except Exception as exc:
            return f"指纹探测失败 · {type(exc).__name__}"

    @staticmethod
    async def _read_balance_text(page: Any) -> str:
        selectors = (
            "[data-balance]",
            ".balance",
            ".wallet",
            ".amount",
            "text=/余额|Balance|额度/",
        )
        for selector in selectors:
            try:
                text = await page.locator(selector).first.inner_text(timeout=800)
                if text:
                    return text.strip()[:40]
            except Exception:
                continue
        return "未识别"

    @staticmethod
    def _looks_like_embedded_game(url: str, screenshot: bytes) -> bool:
        if "/home/embedded" not in str(url):
            return False
        return len(screenshot or b"") > 50_000

    @staticmethod
    def _page_stage(url: str, game_visible: bool) -> str:
        lowered = str(url).lower()
        if "/home/embedded" in lowered and game_visible:
            return "game"
        if "/home/embedded" in lowered:
            return "embedded_pending"
        if "captcha" in lowered or "botion" in lowered:
            return "captcha"
        return "login_or_lobby"
