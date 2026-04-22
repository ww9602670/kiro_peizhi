"""Shared market runtime for cross-account current-install snapshots."""

from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import urlsplit, urlunsplit

from app.engine.adapters.base import InstallInfo
from app.models import db_ops

logger = logging.getLogger(__name__)

DEFAULT_SHARED_MARKET_FRESHNESS_SECONDS = 15
DEFAULT_COLLECTOR_INTERVAL_SECONDS = 3.0
DEFAULT_COLLECTOR_ERROR_BACKOFF_SECONDS = 5.0
PUBLIC_STATE_SHARED_HIT = "shared_hit"
PUBLIC_STATE_LOCAL_FALLBACK = "local_fallback"
PUBLIC_STATE_PROCESSING = "processing"
_PUBLIC_MARKET_STATES = {
    PUBLIC_STATE_SHARED_HIT,
    PUBLIC_STATE_LOCAL_FALLBACK,
    PUBLIC_STATE_PROCESSING,
}


def normalize_market_url(raw_url: str | None) -> str:
    """Normalize platform url into a stable pool key."""
    url = (raw_url or "").strip()
    if not url:
        return ""
    try:
        parsed = urlsplit(url)
    except Exception:
        return ""
    if not parsed.scheme or not parsed.netloc:
        return ""
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    path = (parsed.path or "").rstrip("/")
    return urlunsplit((scheme, netloc, path, "", ""))


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _safe_text(value: object) -> str:
    return "" if value is None else str(value)


def normalize_shared_market_state(
    value: object,
    *,
    default: str = PUBLIC_STATE_PROCESSING,
) -> str:
    text = _safe_text(value).strip().lower()
    if text in _PUBLIC_MARKET_STATES:
        return text
    return default if default in _PUBLIC_MARKET_STATES else PUBLIC_STATE_PROCESSING


def _attach_market_data_state(install: InstallInfo, state: str) -> InstallInfo:
    normalized = normalize_shared_market_state(state)
    # Keep both names for compatibility during rollout.
    setattr(install, "market_data_state", normalized)
    setattr(install, "shared_market_state", normalized)
    return install


def _parse_fetched_at(value: object) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


@dataclass(slots=True)
class SharedMarketSnapshot:
    shared_group_id: int
    issue: str
    state: int
    close_countdown_sec: int
    open_countdown_sec: int
    pre_issue: str
    pre_result: str
    fetched_at: Optional[datetime]
    source_status: str = "ok"

    def to_install(self) -> InstallInfo:
        return InstallInfo(
            issue=self.issue,
            state=self.state,
            close_countdown_sec=self.close_countdown_sec,
            open_countdown_sec=self.open_countdown_sec,
            pre_issue=self.pre_issue,
            pre_result=self.pre_result,
            is_new_issue=False,
        )


class SharedMarketContracts:
    """Frozen db-op contract bridge for shared market runtime."""

    def __init__(self, db: Any) -> None:
        self._db = db
        self._missing_contracts_reported: set[str] = set()

    async def group_resolve_by_url(
        self,
        *,
        platform_type: str,
        normalized_url: str,
    ) -> Optional[int]:
        result = await self._invoke_with_variants(
            "shared_market_group_resolve_by_url",
            [
                {"platform_type": platform_type, "normalized_url": normalized_url},
                {"line_code": platform_type, "normalized_url": normalized_url},
                {"platform_type": platform_type, "url": normalized_url},
                {"line_code": platform_type, "url": normalized_url},
                {"normalized_url": normalized_url},
                {"url": normalized_url},
            ],
        )
        if result is None:
            return None
        if isinstance(result, dict):
            group_id = result.get("shared_group_id") or result.get("group_id") or result.get("id")
            return _safe_int(group_id, 0) or None
        group_id = _safe_int(result, 0)
        return group_id or None

    async def snapshot_upsert(
        self,
        *,
        shared_group_id: int,
        install: InstallInfo,
        source_status: str,
    ) -> None:
        fetched_at_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        await self._invoke_with_variants(
            "shared_market_snapshot_upsert",
            [
                {
                    "shared_group_id": shared_group_id,
                    "issue": install.issue,
                    "state": install.state,
                    "close_countdown_sec": install.close_countdown_sec,
                    "open_countdown_sec": install.open_countdown_sec,
                    "pre_issue": install.pre_issue,
                    "pre_result": install.pre_result,
                    "source_status": source_status,
                    "fetched_at": fetched_at_text,
                },
                {
                    "shared_group_id": shared_group_id,
                    "installments": install.issue,
                    "state": install.state,
                    "close_timestamp": install.close_countdown_sec,
                    "open_timestamp": install.open_countdown_sec,
                    "pre_installments": install.pre_issue,
                    "pre_lottery_result": install.pre_result,
                    "status": source_status,
                    "fetched_at": fetched_at_text,
                },
            ],
            allow_failure=True,
        )

    async def snapshot_get_latest(self, *, shared_group_id: int) -> Optional[SharedMarketSnapshot]:
        row = await self._invoke_with_variants(
            "shared_market_snapshot_get_latest",
            [
                {"shared_group_id": shared_group_id},
                {"group_id": shared_group_id},
            ],
        )
        if not isinstance(row, dict):
            return None
        issue = _safe_text(row.get("issue") or row.get("installments"))
        if not issue:
            return None
        return SharedMarketSnapshot(
            shared_group_id=_safe_int(
                row.get("shared_group_id") or row.get("group_id") or shared_group_id,
                shared_group_id,
            ),
            issue=issue,
            state=_safe_int(row.get("state"), 0),
            close_countdown_sec=_safe_int(
                row.get("close_countdown_sec") or row.get("close_timestamp"),
                0,
            ),
            open_countdown_sec=_safe_int(
                row.get("open_countdown_sec") or row.get("open_timestamp"),
                0,
            ),
            pre_issue=_safe_text(row.get("pre_issue") or row.get("pre_installments")),
            pre_result=_safe_text(row.get("pre_result") or row.get("pre_lottery_result")),
            fetched_at=_parse_fetched_at(
                row.get("fetched_at")
                or row.get("updated_at")
                or row.get("snapshot_fetched_at")
            ),
            source_status=_safe_text(row.get("source_status") or row.get("status") or "ok").lower(),
        )

    async def uncovered_url_touch(
        self,
        *,
        platform_type: str,
        normalized_url: str,
    ) -> None:
        await self._invoke_with_variants(
            "shared_market_uncovered_url_touch",
            [
                {"platform_type": platform_type, "normalized_url": normalized_url},
                {"line_code": platform_type, "normalized_url": normalized_url},
                {"platform_type": platform_type, "url": normalized_url},
                {"line_code": platform_type, "url": normalized_url},
            ],
            allow_failure=True,
        )

    async def _invoke_with_variants(
        self,
        contract_name: str,
        variants: list[dict[str, Any]],
        *,
        allow_failure: bool = False,
    ) -> Any:
        fn = getattr(db_ops, contract_name, None)
        if fn is None:
            if contract_name not in self._missing_contracts_reported:
                self._missing_contracts_reported.add(contract_name)
                logger.warning("shared_market_contract_missing contract=%s", contract_name)
            return None

        last_type_error: Optional[TypeError] = None
        for kwargs in variants:
            try:
                result = fn(self._db, **kwargs)
            except TypeError as exc:
                last_type_error = exc
                continue
            if inspect.isawaitable(result):
                return await result
            return result

        if allow_failure:
            return None
        if last_type_error is not None:
            logger.warning(
                "shared_market_contract_signature_mismatch contract=%s detail=%s",
                contract_name,
                last_type_error,
            )
        return None


@dataclass(slots=True)
class _CollectorState:
    shared_group_id: int
    owner_key: str
    stop_event: asyncio.Event
    task: asyncio.Task[None]


class SharedMarketRuntime:
    """Shared current-install pool with collector lifecycle and fallback."""

    def __init__(
        self,
        *,
        db: Any,
        contracts: Optional[SharedMarketContracts] = None,
        freshness_seconds: int = DEFAULT_SHARED_MARKET_FRESHNESS_SECONDS,
        collector_interval_seconds: float = DEFAULT_COLLECTOR_INTERVAL_SECONDS,
        collector_error_backoff_seconds: float = DEFAULT_COLLECTOR_ERROR_BACKOFF_SECONDS,
    ) -> None:
        self._contracts = contracts or SharedMarketContracts(db)
        self._freshness_seconds = max(1, int(freshness_seconds))
        self._collector_interval_seconds = max(0.5, float(collector_interval_seconds))
        self._collector_error_backoff_seconds = max(0.5, float(collector_error_backoff_seconds))
        self._collectors: dict[int, _CollectorState] = {}
        self._lock = asyncio.Lock()

    async def resolve_install(
        self,
        *,
        platform_type: str,
        platform_url: str | None,
        owner_key: str,
        fetch_local_install: Callable[[], Awaitable[InstallInfo]],
    ) -> InstallInfo:
        normalized_url = normalize_market_url(platform_url)
        if not normalized_url:
            install = await fetch_local_install()
            return _attach_market_data_state(install, PUBLIC_STATE_LOCAL_FALLBACK)

        shared_group_id = await self._contracts.group_resolve_by_url(
            platform_type=platform_type,
            normalized_url=normalized_url,
        )
        if shared_group_id is None:
            logger.info(
                "shared_group_miss platform_type=%s normalized_url=%s",
                platform_type,
                normalized_url,
            )
            await self._contracts.uncovered_url_touch(
                platform_type=platform_type,
                normalized_url=normalized_url,
            )
            logger.info(
                "shared_uncovered_url_recorded platform_type=%s normalized_url=%s",
                platform_type,
                normalized_url,
            )
            install = await fetch_local_install()
            return _attach_market_data_state(install, PUBLIC_STATE_LOCAL_FALLBACK)

        logger.info(
            "shared_group_hit group_id=%d platform_type=%s",
            shared_group_id,
            platform_type,
        )
        await self._ensure_collector(
            shared_group_id=shared_group_id,
            owner_key=owner_key,
            fetch_local_install=fetch_local_install,
        )

        snapshot = await self._contracts.snapshot_get_latest(shared_group_id=shared_group_id)
        if snapshot is None:
            return await self._fallback_local(
                shared_group_id=shared_group_id,
                reason="missing",
                fetch_local_install=fetch_local_install,
            )

        if snapshot.source_status not in {"ok", ""}:
            return await self._fallback_local(
                shared_group_id=shared_group_id,
                reason=f"source_{snapshot.source_status}",
                fetch_local_install=fetch_local_install,
            )

        now = datetime.now(snapshot.fetched_at.tzinfo) if snapshot.fetched_at else datetime.now()
        if snapshot.fetched_at is None:
            return await self._fallback_local(
                shared_group_id=shared_group_id,
                reason="no_fetched_at",
                fetch_local_install=fetch_local_install,
            )
        age_seconds = (now - snapshot.fetched_at).total_seconds()
        if age_seconds > self._freshness_seconds:
            logger.info(
                "shared_snapshot_stale group_id=%d age_seconds=%.2f freshness=%d",
                shared_group_id,
                age_seconds,
                self._freshness_seconds,
            )
            return await self._fallback_local(
                shared_group_id=shared_group_id,
                reason="stale",
                fetch_local_install=fetch_local_install,
            )

        return _attach_market_data_state(snapshot.to_install(), PUBLIC_STATE_SHARED_HIT)

    async def release_owner(self, owner_key: str) -> None:
        if not owner_key:
            return
        await self._stop_collectors(lambda state: state.owner_key == owner_key)

    async def shutdown(self) -> None:
        await self._stop_collectors(lambda _state: True)

    async def _fallback_local(
        self,
        *,
        shared_group_id: int,
        reason: str,
        fetch_local_install: Callable[[], Awaitable[InstallInfo]],
        market_data_state: str = PUBLIC_STATE_PROCESSING,
    ) -> InstallInfo:
        logger.info(
            "shared_snapshot_fallback group_id=%d reason=%s",
            shared_group_id,
            reason,
        )
        install = await fetch_local_install()
        await self._contracts.snapshot_upsert(
            shared_group_id=shared_group_id,
            install=install,
            source_status="ok",
        )
        return _attach_market_data_state(install, market_data_state)

    async def _ensure_collector(
        self,
        *,
        shared_group_id: int,
        owner_key: str,
        fetch_local_install: Callable[[], Awaitable[InstallInfo]],
    ) -> None:
        async with self._lock:
            existing = self._collectors.get(shared_group_id)
            if existing and not existing.task.done():
                return

            stop_event = asyncio.Event()
            task = asyncio.create_task(
                self._collector_loop(
                    shared_group_id=shared_group_id,
                    owner_key=owner_key,
                    stop_event=stop_event,
                    fetch_local_install=fetch_local_install,
                ),
                name=f"shared-market-collector-{shared_group_id}",
            )
            self._collectors[shared_group_id] = _CollectorState(
                shared_group_id=shared_group_id,
                owner_key=owner_key,
                stop_event=stop_event,
                task=task,
            )

    async def _stop_collectors(
        self,
        predicate: Callable[[_CollectorState], bool],
    ) -> None:
        targets: list[_CollectorState] = []
        async with self._lock:
            for group_id, state in list(self._collectors.items()):
                if not predicate(state):
                    continue
                state.stop_event.set()
                self._collectors.pop(group_id, None)
                targets.append(state)

        for state in targets:
            state.task.cancel()
            try:
                await state.task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception(
                    "shared_collector_stop_error group_id=%d owner=%s",
                    state.shared_group_id,
                    state.owner_key,
                )

    async def _collector_loop(
        self,
        *,
        shared_group_id: int,
        owner_key: str,
        stop_event: asyncio.Event,
        fetch_local_install: Callable[[], Awaitable[InstallInfo]],
    ) -> None:
        last_install: Optional[InstallInfo] = None
        while not stop_event.is_set():
            sleep_seconds = self._collector_interval_seconds
            try:
                install = await fetch_local_install()
                last_install = install
                await self._contracts.snapshot_upsert(
                    shared_group_id=shared_group_id,
                    install=install,
                    source_status="ok",
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "shared_collector_error group_id=%d owner=%s",
                    shared_group_id,
                    owner_key,
                )
                logger.info(
                    "shared_collector_error group_id=%d owner=%s",
                    shared_group_id,
                    owner_key,
                )
                if last_install is not None:
                    await self._contracts.snapshot_upsert(
                        shared_group_id=shared_group_id,
                        install=last_install,
                        source_status="error",
                    )
                sleep_seconds = self._collector_error_backoff_seconds

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=sleep_seconds)
            except asyncio.TimeoutError:
                continue
