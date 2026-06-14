"""Pure backend coordinator for hedge execution shadow planning."""

from __future__ import annotations

import asyncio
import inspect
import random
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

from bet_desktop.core.addon_four import AddonFourController
from bet_desktop.core.execution_events import ExecutionEvent, ExecutionEventType
from bet_desktop.core.group_action import GroupActionRequest
from bet_desktop.core.hedge_execution_config import HedgeExecutionConfig
from bet_desktop.core.hedge_plan import (
    AccountExecutionSnapshot,
    HedgeBetPlan,
    HedgePlanPolicy,
    RandomHedgePlanGenerator,
    decide_bet_window,
    evaluate_preflight,
    legal_amounts,
)
from bet_desktop.models.state_temporal_guard import TemporalStateSnapshot, now_ms
from bet_desktop.vision.live_game_regions import BASE_SIZE as LIVE_GAME_BASE_SIZE
from bet_desktop.vision.live_game_regions import LIVE_GAME_REGIONS

MAX_SYNC_SNAPSHOT_AGE_MS = 2500
MAX_SYNC_CAPTURE_SPREAD_MS = 1500
MAX_SYNC_COUNTDOWN_SKEW_SECONDS = 1.5
SYNC_BLOCK_EVENT_THROTTLE_MS = 2_500


class HedgeExecutionState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"
    WAITING_NEXT_ROUND = "waiting_next_round"
    PRECHECKING = "prechecking"
    PLANNING = "planning"
    SHADOW_EXECUTING = "shadow_executing"
    EXECUTING = "executing"
    RECOVERING = "recovering"
    WAITING_SETTLEMENT = "waiting_settlement"
    STOPPED = "stopped"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class HedgeShadowPlan:
    """Safe shadow plan returned by the orchestrator."""

    round_id: str
    plan: HedgeBetPlan
    addon_four: dict[str, Any] | None = None
    countdown_seconds: float = 0.0


class HedgeExecutionOrchestrator:
    """Pure logic orchestrator that produces safe shadow execution plans."""

    def __init__(
        self,
        *,
        event_listener: Callable[[ExecutionEvent], None] | None = None,
        shadow_executor: Any = None,
        shadow_logger: Callable[[str], None] | None = None,
        shadow_session_manager: Any = None,
        rng: random.Random | None = None,
    ) -> None:
        self._event_listener = event_listener
        self._shadow_executor = shadow_executor
        self._shadow_logger = shadow_logger
        self._shadow_session_manager = shadow_session_manager
        self._rng = rng or random.Random(0)
        self._config: HedgeExecutionConfig | None = None
        self._state: HedgeExecutionState = HedgeExecutionState.IDLE
        self._state_reason: str = "not_started"
        self._addon_controller = AddonFourController(rng=self._rng)
        self._events: list[ExecutionEvent] = []
        self._latest_temporal_states: dict[str, TemporalStateSnapshot] = {}
        self._current_round_id: str | None = None
        self._active_round_id: str | None = None
        self._last_sync_block_signature: tuple[Any, ...] | None = None
        self._last_sync_block_emit_ms = 0

    @property
    def state(self) -> HedgeExecutionState:
        return self._state

    @property
    def state_reason(self) -> str:
        return self._state_reason

    @property
    def events(self) -> list[ExecutionEvent]:
        return list(self._events)

    def clear_events(self) -> None:
        self._events.clear()

    def start(self, config: HedgeExecutionConfig) -> None:
        if self._state not in (
            HedgeExecutionState.STOPPED,
            HedgeExecutionState.IDLE,
            HedgeExecutionState.BLOCKED,
        ):
            self._emit(
                ExecutionEventType.EXECUTION_BLOCKED,
                state=self._state.value,
                message="start_rejected",
                safe_summary={"reason": f"start_not_allowed_in_{self._state.value}"},
            )
            return

        self._config = config if isinstance(config, HedgeExecutionConfig) else HedgeExecutionConfig()
        self._state = HedgeExecutionState.RUNNING
        self._state_reason = "started"
        self._current_round_id = None
        self._active_round_id = None
        self._latest_temporal_states = {}
        self._last_sync_block_signature = None
        self._last_sync_block_emit_ms = 0
        self._addon_controller = AddonFourController(
            mode=config.addon_four_mode,
            frequency=config.addon_four_frequency,
            rng=self._rng,
        )
        self._emit(
            ExecutionEventType.EXECUTION_STARTED,
            state=self._state.value,
            message="execution_started",
            safe_summary={"config": self._config.to_safe_dict()},
        )

    def pause(self) -> None:
        if self._state in {
            HedgeExecutionState.RUNNING,
            HedgeExecutionState.PRECHECKING,
            HedgeExecutionState.PLANNING,
            HedgeExecutionState.SHADOW_EXECUTING,
            HedgeExecutionState.EXECUTING,
            HedgeExecutionState.RECOVERING,
            HedgeExecutionState.WAITING_NEXT_ROUND,
            HedgeExecutionState.WAITING_SETTLEMENT,
        }:
            self._state = HedgeExecutionState.PAUSED
            self._state_reason = "paused_by_user"
            self._emit(
                ExecutionEventType.EXECUTION_PAUSED,
                state=self._state.value,
                message="execution_paused",
            )
            return

        self._emit(
            ExecutionEventType.EXECUTION_BLOCKED,
            state=self._state.value,
            message="pause_rejected",
            safe_summary={"reason": f"pause_not_allowed_in_{self._state.value}"},
        )

    def stop(self) -> None:
        if self._state in (HedgeExecutionState.STOPPED, HedgeExecutionState.IDLE):
            self._emit(
                ExecutionEventType.EXECUTION_STOPPED,
                state=self._state.value,
                message="execution_stopped",
            )
            return

        self._state = HedgeExecutionState.STOPPING
        self._state_reason = "stopping_by_user"
        self._emit(
            ExecutionEventType.EXECUTION_STOPPED,
            state=self._state.value,
            message="瀵瑰啿褰卞瓙鎵ц姝ｅ湪鍋滄",
        )
        self._state = HedgeExecutionState.STOPPED
        self._state_reason = "stopped_by_user"

    def on_temporal_state(self, payload: TemporalStateSnapshot | dict[str, Any]) -> None:
        snapshot = payload
        if isinstance(payload, dict):
            snapshot = TemporalStateSnapshot.from_mapping(payload)
        if not isinstance(snapshot, TemporalStateSnapshot):
            return

        self._latest_temporal_states[snapshot.instance_id] = snapshot

        if self._state in (
            HedgeExecutionState.RUNNING,
            HedgeExecutionState.WAITING_SETTLEMENT,
            HedgeExecutionState.WAITING_NEXT_ROUND,
        ):
            self.evaluate_current_round()

    def evaluate_current_round(self) -> HedgeShadowPlan | None:
        config = self._config
        if config is None or self._state not in (
            HedgeExecutionState.RUNNING,
            HedgeExecutionState.WAITING_NEXT_ROUND,
        ):
            return None

        required_ids = (config.main_account_id, *config.active_sub_account_ids)
        missing = [instance_id for instance_id in required_ids if instance_id not in self._latest_temporal_states]
        if missing:
            self._state = HedgeExecutionState.WAITING_NEXT_ROUND
            self._state_reason = "waiting_temporal_state"
            self._emit(
                ExecutionEventType.EXECUTION_BLOCKED,
                state=self._state.value,
                message="缂哄皯涓诲彿鎴栧彲鐢ㄥ壇鍙风殑鏃堕棿蹇収",
                safe_summary={
                    "reason": "waiting_temporal_state",
                    "missing_instances": tuple(missing),
                    "excluded_account_ids": tuple(config.excluded_account_ids),
                },
            )
            return None

        main_snapshot = self._latest_temporal_states[config.main_account_id]
        round_id = self._extract_round_id(main_snapshot)

        sync_block = self._check_execution_sync_gate(config=config, required_ids=required_ids)
        if sync_block is not None:
            self._state = HedgeExecutionState.WAITING_NEXT_ROUND
            self._state_reason = "waiting_synchronized_snapshots"
            self._emit_sync_blocked(
                ExecutionEventType.EXECUTION_BLOCKED,
                state=self._state.value,
                message="waiting_synchronized_snapshots",
                safe_summary=sync_block,
            )
            return None

        if self._active_round_id == round_id:
            return None

        self._current_round_id = round_id
        self._state = HedgeExecutionState.PRECHECKING
        self._state_reason = "precheck_in_progress"
        self._emit(
            ExecutionEventType.ROUND_PRECHECK_STARTED,
            state=self._state.value,
            message="round_precheck_started",
            safe_summary={"round_id": round_id},
        )

        if not 2 <= config.plan_sub_account_count <= 3:
            self._state = HedgeExecutionState.WAITING_NEXT_ROUND
            self._state_reason = "unsupported_sub_account_count"
            self._emit(
                ExecutionEventType.EXECUTION_BLOCKED,
                state=self._state.value,
                message="鍓彿鏁颁笉瓒筹紝鏃犳硶鐢熸垚瀵瑰啿璁″垝",
                safe_summary={"round_id": round_id, "sub_account_count": config.plan_sub_account_count},
            )
            return None

        policy = config.to_plan_policy()
        snapshots_for_plan, safety_blocks, fact_sources = self._build_account_snapshots(
            config=config,
            snapshots=self._latest_temporal_states,
        )

        if safety_blocks:
            self._state = HedgeExecutionState.WAITING_NEXT_ROUND
            self._state_reason = "safety_gate_blocked"
            for account_id, reasons in safety_blocks.items():
                self._emit(
                    ExecutionEventType.ACCOUNT_EXCLUDED,
                    state=self._state.value,
                    message="鍧愭爣/闄愰鏍￠獙澶辫触",
                    safe_summary={
                        "account_id": account_id,
                        "reasons": tuple(reasons),
                        "execution_facts": fact_sources.get(account_id, {}),
                    },
                )
            self._emit(
                ExecutionEventType.EXECUTION_BLOCKED,
                state=self._state.value,
                message="坐标或限额缺失，当前局已阻断",
                safe_summary={
                    "round_id": round_id,
                    "blocked_accounts": tuple(safety_blocks.keys()),
                    "reasons": ("fail_closed",),
                    "execution_facts": fact_sources,
                },
            )
            return None

        try:
            addon_decision = self._addon_controller.should_fire(round_id=round_id)
            plan = self._build_shadow_plan(
                policy=policy,
                config=config,
                addon_decision=addon_decision,
            )
        except Exception as exc:
            self._state = HedgeExecutionState.BLOCKED
            self._state_reason = "plan_generation_failed"
            self._emit(
                ExecutionEventType.EXECUTION_BLOCKED,
                state=self._state.value,
                message="鐢熸垚瀵瑰啿鏂规澶辫触",
                safe_summary={"round_id": round_id, "error": str(exc)},
            )
            return None

        if not snapshots_for_plan:
            self._state = HedgeExecutionState.BLOCKED
            self._state_reason = "no_account_snapshot"
            self._emit(
                ExecutionEventType.EXECUTION_BLOCKED,
                state=self._state.value,
                message="missing_account_snapshot",
                safe_summary={"round_id": round_id},
            )
            return None

        plan_main_snapshot = next(
            (item for item in snapshots_for_plan if item.account_id == config.main_account_id),
            main_snapshot,
        )
        countdown = float(plan_main_snapshot.countdown_seconds)
        preflight = evaluate_preflight(
            snapshots_for_plan,
            main_account_id=config.main_account_id,
            planned_amount_by_account=plan.plan.amount_by_account(),
            policy=policy,
            now_ms=now_ms(),
        )
        if not preflight.ok:
            self._state = HedgeExecutionState.WAITING_NEXT_ROUND
            self._state_reason = preflight.reason
            for item in preflight.eligibilities:
                if not item.ok:
                    self._emit(
                        ExecutionEventType.ACCOUNT_EXCLUDED,
                        state=self._state.value,
                        message="璐︽埛棰勬涓嶉€氳繃",
                        safe_summary={
                            "account_id": item.account_id,
                            "reasons": tuple(item.reasons),
                            "round_id": round_id,
                        },
                    )
            self._emit(
                ExecutionEventType.EXECUTION_BLOCKED,
                state=self._state.value,
                message="褰撳墠灞€娆￠妫€闃绘柇",
                safe_summary={"round_id": round_id, "reason": preflight.reason, "usable_accounts": tuple(preflight.usable_account_ids)},
            )
            return None

        window_decision = decide_bet_window(
            betting_open=countdown >= 0,
            countdown_seconds=countdown,
            total_chip_steps=plan.plan.total_chip_steps,
            elapsed_ms=preflight.elapsed_ms,
            policy=policy,
        )
        if window_decision.action == "pause_next_round":
            self._state = HedgeExecutionState.WAITING_NEXT_ROUND
            self._state_reason = window_decision.reason
            self._emit(
                ExecutionEventType.ROUND_SKIPPED,
                state=self._state.value,
                message="褰撳墠灞€娆＄獥鍙ｄ笉瓒筹紝璺宠繃",
                safe_summary={"round_id": round_id, "reason": window_decision.reason, "countdown_seconds": countdown},
            )
            return None

        if window_decision.action == "low_step_only":
            self._state = HedgeExecutionState.WAITING_NEXT_ROUND
            self._state_reason = window_decision.reason
            self._emit(
                ExecutionEventType.EXECUTION_BLOCKED,
                state=self._state.value,
                message="褰撳墠灞€娆¤繘鍏ヤ綆姝ヨ繘瀹夊叏鍒嗘敮",
                safe_summary={"round_id": round_id, "reason": window_decision.reason},
            )
            return None

        self._state = HedgeExecutionState.PLANNING
        self._state_reason = "planning"
        addon_summary = dict(plan.addon_four) if plan.addon_four else {"enabled": False, "reason": "mode_off"}
        if "enabled" in addon_summary:
            addon_summary["enabled"] = bool(addon_summary["enabled"])

        addon_msg = (
            f"检测到额外风险动作（补4）：{addon_summary.get('reason', '')}，方向={addon_summary.get('side', '-')}"
            if addon_summary.get("enabled")
            else "未触发补4动作"
        )
        self._emit(
            ExecutionEventType.ADDON_FOUR_DECIDED,
            state=self._state.value,
            message=addon_msg,
            safe_summary={"round_id": round_id, "addon": addon_summary},
        )

        plan_legs = [
            {
                "account_id": leg.account_id,
                "role": leg.role,
                "side": leg.side,
                "amount": leg.amount,
                "chip_sequence": list(leg.chip_sequence),
            }
            for leg in plan.plan.legs
        ]
        self._emit(
            ExecutionEventType.PLAN_GENERATED,
            state=self._state.value,
            message="plan_generated",
            safe_summary={
                "round_id": round_id,
                "main_account_id": plan.plan.main_account_id,
                "main_side": plan.plan.main_side,
                "opposite_side": plan.plan.opposite_side,
                "main_amount": plan.plan.main_amount,
                "sub_amounts": tuple(plan.plan.amount_by_account().values()),
                "legs": tuple(plan_legs),
                "countdown_seconds": countdown,
                "addon_four": plan.addon_four,
            },
        )

        self._state = HedgeExecutionState.SHADOW_EXECUTING
        self._state_reason = "shadow_execution_in_progress"
        self._emit(
            ExecutionEventType.BET_EXECUTION_STARTED,
            state=self._state.value,
            message="shadow_execution_started",
            safe_summary={
                "round_id": round_id,
                "account_count": len(plan_legs),
                "mode": "shadow_state_machine",
                "live_clicks": 0,
            },
        )
        shadow_executor_summary = self._execute_shadow_run_if_bound(
            config=config,
            plan=plan,
            round_id=round_id,
        )
        for leg in plan.plan.legs:
            self._emit(
                ExecutionEventType.BET_LEG_SUCCEEDED,
                state=self._state.value,
                message="bet_leg_shadow_validated",
                safe_summary={
                    "round_id": round_id,
                    "account_id": leg.account_id,
                    "side": leg.side,
                    "amount": leg.amount,
                    "shadow_executor": shadow_executor_summary,
                },
            )

        self._emit(
            ExecutionEventType.PROFIT_LOSS_UPDATED,
            state=self._state.value,
            message="shadow_profit_loss_preview_updated",
            safe_summary={
                "round_id": round_id,
                "main_account_id": plan.plan.main_account_id,
                "main_amount": plan.plan.main_amount,
                "sub_amount_total": plan.plan.sub_total,
            },
        )

        self._state = HedgeExecutionState.WAITING_SETTLEMENT
        self._state_reason = "awaiting_next_round_to_enter"
        self._active_round_id = round_id
        self._emit(
            ExecutionEventType.SETTLEMENT_DETECTED,
            state=self._state.value,
            message="璁″垝宸叉彁浜わ紙褰卞瓙锛夛紝绛夊緟缁撶畻淇″彿",
            safe_summary={"round_id": round_id},
        )

        self._state = HedgeExecutionState.WAITING_NEXT_ROUND
        self._state_reason = "awaiting_next_round"
        self._emit(
            ExecutionEventType.EXECUTION_BLOCKED,
            state=self._state.value,
            message="鏈眬璁″垝宸茶惤鍦帮紝绛夊緟涓嬩竴灞€",
            safe_summary={"round_id": round_id},
        )
        return plan

    def _execute_shadow_run_if_bound(
        self,
        *,
        config: HedgeExecutionConfig,
        plan: HedgeShadowPlan,
        round_id: str,
    ) -> dict[str, Any]:
        executor = self._shadow_executor
        if executor is None:
            summary = {"status": "not_bound"}
            self._emit_shadow_status(summary)
            return summary
        execute_shadow_run = getattr(executor, "execute_shadow_run", None)
        if execute_shadow_run is None:
            summary = {"status": "missing_execute_shadow_run"}
            self._emit_shadow_status(summary)
            return summary

        sub_amounts = tuple(leg.amount for leg in plan.plan.legs if leg.role == "sub")
        if len(sub_amounts) != 3:
            summary = {
                "status": "skipped",
                "reason": "requires_three_sub_accounts",
                "sub_account_count": len(sub_amounts),
            }
            self._emit_shadow_status(summary)
            return summary

        request = GroupActionRequest(
            batch_id=round_id,
            main_account_id=config.main_account_id,
            main_side=plan.plan.main_side,
            opposite_side=plan.plan.opposite_side,
            main_amount=plan.plan.main_amount,
            denominations=tuple(config.denominations),
            sub_amounts=sub_amounts,  # type: ignore[arg-type]
        )
        try:
            shadow_kwargs: dict[str, Any] = {}
            if self._shadow_logger is not None:
                shadow_kwargs["logger"] = self._shadow_logger
            if self._shadow_session_manager is not None:
                shadow_kwargs["session_manager"] = self._shadow_session_manager
            try:
                result = execute_shadow_run(request, **shadow_kwargs)
            except TypeError as exc:
                if not shadow_kwargs or "unexpected keyword" not in str(exc):
                    raise
                result = execute_shadow_run(request)
            if inspect.isawaitable(result):
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    result = asyncio.run(result)
                else:
                    loop.create_task(result)
                    return {"status": "scheduled"}
            return {
                "status": "completed",
                "phase": getattr(result, "phase", ""),
                "safe_summary": dict(getattr(result, "safe_summary", {}) or {}),
            }
        except Exception as exc:
            summary = {"status": "failed", "error": str(exc)}
            self._emit_shadow_status(summary)
            return summary

    def _emit_shadow_status(self, summary: dict[str, Any]) -> None:
        if self._shadow_logger is None:
            return
        try:
            self._shadow_logger(f"[SHADOW_DRY_RUN] executor_status={summary}")
        except Exception:
            return

    def _build_shadow_plan(
        self,
        *,
        policy: HedgePlanPolicy,
        config: HedgeExecutionConfig,
        addon_decision: Any,
    ) -> HedgeShadowPlan:
        round_id = self._current_round_id or "-"
        main_amount = self._pick_main_amount(config, policy=policy)
        generator = RandomHedgePlanGenerator(policy, rng=self._rng)
        main_side, opposite_side = self._resolve_hedge_sides(config.hedge_model)
        plan = generator.generate(
            main_account_id=config.main_account_id,
            sub_account_ids=config.active_sub_account_ids,
            main_side=main_side,
            opposite_side=opposite_side,
            main_amount=main_amount,
        )

        addon_four: dict[str, Any] | None = None
        if addon_decision and getattr(addon_decision, "enabled", False):
            addon_four = {
                "enabled": True,
                "side": str(addon_decision.side),
                "amount": int(addon_decision.amount),
                "reason": str(addon_decision.reason),
            }

        return HedgeShadowPlan(
            round_id=round_id,
            plan=plan,
            addon_four=addon_four,
            countdown_seconds=float(policy.normal_min_countdown_seconds),
        )

    def _pick_main_amount(self, config: HedgeExecutionConfig, *, policy: HedgePlanPolicy) -> int:
        amounts = legal_amounts(policy)
        candidates = [value for value in amounts if config.main_amount_min <= value <= config.main_amount_max]
        if not candidates:
            raise ValueError("no legal plan amount for configured range")
        if config.main_amount_min == config.main_amount_max:
            return config.main_amount_min
        self._rng.shuffle(candidates)
        return candidates[0]

    def _build_account_snapshots(
        self,
        *,
        config: HedgeExecutionConfig,
        snapshots: dict[str, TemporalStateSnapshot],
    ) -> tuple[
        tuple[AccountExecutionSnapshot, ...],
        dict[str, tuple[str, ...]],
        dict[str, dict[str, Any]],
    ]:
        result: list[AccountExecutionSnapshot] = []
        safety_blocks: dict[str, list[str]] = {}
        fact_sources: dict[str, dict[str, Any]] = {}
        required_ids = (config.main_account_id, *config.active_sub_account_ids)
        current_ms = now_ms()

        for instance_id in required_ids:
            snapshot = snapshots[instance_id]
            summary = snapshot.safe_summary or {}
            role = "main" if instance_id == config.main_account_id else "sub"
            reasons: list[str] = []
            execution_online = self._snapshot_execution_online(snapshot, current_ms=current_ms)

            limit_bounds, limit_source = self._resolve_limit_bounds(summary)
            if limit_bounds is None:
                reasons.append("limit_not_verified")
                table_min, table_max = (0, 0)
            else:
                table_min, table_max = limit_bounds

            (
                coord_ready,
                chip_coord_ready,
                side_coord_ready,
                coordinate_source,
            ) = self._resolve_coordinate_readiness(summary)
            if not coord_ready:
                reasons.append("coordinate_not_verified")
            if not chip_coord_ready:
                reasons.append("chip_coordinate_not_verified")
            if not side_coord_ready:
                reasons.append("side_coordinate_not_verified")

            fact_sources[instance_id] = {
                "limit_source": limit_source,
                "limit_bounds": tuple(limit_bounds) if limit_bounds is not None else None,
                "coordinate_source": coordinate_source,
                "coordinate_ready": bool(coord_ready),
                "chip_coordinates_ready": bool(chip_coord_ready),
                "side_coordinates_ready": bool(side_coord_ready),
            }

            if reasons:
                safety_blocks[instance_id] = tuple(dict.fromkeys(reasons))

            result.append(
                AccountExecutionSnapshot(
                    account_id=instance_id,
                    role=role,
                    online=execution_online,
                    room_id=str(summary.get("room_id", "")),
                    table_id=str(summary.get("table_id", "")),
                    round_id=self._normalize_round_group(self._extract_round_id(snapshot)),
                    betting_open=snapshot.exact_countdown >= config.min_execute_countdown_seconds,
                    countdown_seconds=float(snapshot.exact_countdown),
                    balance=self._parse_balance(str(snapshot.ocr_balance)),
                    table_min=int(table_min),
                    table_max=int(table_max),
                    network_latency_ms=int(summary.get("proxy_latency_ms", summary.get("latency_ms", 0)) or 0),
                    websocket_alive=execution_online,
                    coordinate_ready=coord_ready,
                    chip_coordinates_ready=chip_coord_ready,
                    side_coordinates_ready=side_coord_ready,
                    page_responsive=execution_online,
                    modal_blocking=bool(summary.get("modal_blocking", False)),
                    pending_action=bool(summary.get("pending_action", False)),
                    last_updated_ms=int(snapshot.timestamp_captured_ms),
                )
            )
        return (
            tuple(result),
            {key: tuple(value) for key, value in safety_blocks.items()},
            fact_sources,
        )

    def _check_execution_sync_gate(
        self,
        *,
        config: HedgeExecutionConfig,
        required_ids: tuple[str, ...],
    ) -> dict[str, Any] | None:
        snapshots = [self._latest_temporal_states[instance_id] for instance_id in required_ids]
        current_ms = now_ms()
        reasons: list[str] = []

        snapshot_age_ms = {
            item.instance_id: max(0, current_ms - int(item.timestamp_captured_ms))
            for item in snapshots
        }
        stale_instances = tuple(
            instance_id
            for instance_id, age_ms in snapshot_age_ms.items()
            if age_ms > MAX_SYNC_SNAPSHOT_AGE_MS
        )
        if stale_instances:
            reasons.append("stale_snapshot")

        capture_times = [int(item.timestamp_captured_ms) for item in snapshots]
        capture_spread_ms = max(capture_times) - min(capture_times) if capture_times else 0
        if capture_spread_ms > MAX_SYNC_CAPTURE_SPREAD_MS:
            reasons.append("snapshot_capture_spread")

        offline_instances = tuple(
            item.instance_id
            for item in snapshots
            if not self._snapshot_execution_online(item, current_ms=current_ms)
        )
        if offline_instances:
            reasons.append("offline_or_page_dead")

        room_keys = {item.instance_id: self._extract_room_key(item) for item in snapshots}
        if any(not room for room in room_keys.values()):
            reasons.append("missing_room")
        elif len(set(room_keys.values())) > 1:
            reasons.append("room_mismatch")

        round_keys = {item.instance_id: self._normalize_round_group(self._extract_round_id(item)) for item in snapshots}
        if any(not round_key for round_key in round_keys.values()):
            reasons.append("missing_round")
        elif len(set(round_keys.values())) > 1:
            reasons.append("round_mismatch")

        countdowns = {
            item.instance_id: float(item.exact_countdown)
            for item in snapshots
            if item.exact_countdown >= 0
        }
        if len(countdowns) != len(snapshots):
            reasons.append("missing_countdown")
        elif max(countdowns.values()) - min(countdowns.values()) > MAX_SYNC_COUNTDOWN_SKEW_SECONDS:
            reasons.append("countdown_skew")
        elif any(value < float(config.min_execute_countdown_seconds) for value in countdowns.values()):
            reasons.append("countdown_below_execute_min")

        phases = {
            item.instance_id: phase
            for item in snapshots
            if (phase := self._extract_phase_key(item))
        }
        if len(phases) == len(snapshots) and len(set(phases.values())) > 1:
            reasons.append("phase_mismatch")

        if not reasons:
            return None

        return {
            "reason": "waiting_synchronized_snapshots",
            "reasons": tuple(dict.fromkeys(reasons)),
            "required_instances": tuple(required_ids),
            "excluded_account_ids": tuple(config.excluded_account_ids),
            "snapshot_age_ms": snapshot_age_ms,
            "stale_instances": stale_instances,
            "offline_instances": offline_instances,
            "capture_spread_ms": capture_spread_ms,
            "room_keys": room_keys,
            "round_keys": round_keys,
            "countdowns": countdowns,
            "phases": phases,
            "max_snapshot_age_ms": MAX_SYNC_SNAPSHOT_AGE_MS,
            "max_capture_spread_ms": MAX_SYNC_CAPTURE_SPREAD_MS,
            "max_countdown_skew_seconds": MAX_SYNC_COUNTDOWN_SKEW_SECONDS,
        }

    def _emit_sync_blocked(
        self,
        event_type: ExecutionEventType | str,
        *,
        state: str,
        message: str,
        safe_summary: dict[str, Any],
    ) -> None:
        signature = self._sync_block_signature(safe_summary)
        current_ms = now_ms()
        if current_ms - self._last_sync_block_emit_ms < SYNC_BLOCK_EVENT_THROTTLE_MS:
            self._last_sync_block_signature = signature
            return
        self._last_sync_block_signature = signature
        self._last_sync_block_emit_ms = current_ms
        self._emit(event_type, state=state, message=message, safe_summary=safe_summary)

    @staticmethod
    def _sync_block_signature(summary: dict[str, Any]) -> tuple[Any, ...]:
        return (
            summary.get("reason"),
            tuple(summary.get("reasons") or ()),
            tuple(summary.get("required_instances") or ()),
            tuple(summary.get("excluded_account_ids") or ()),
            tuple(summary.get("stale_instances") or ()),
            tuple(summary.get("offline_instances") or ()),
            tuple(sorted((summary.get("room_keys") or {}).items())),
            tuple(sorted((summary.get("round_keys") or {}).items())),
            tuple(sorted((summary.get("phases") or {}).items())),
        )

    @staticmethod
    def _parse_limit_bounds(summary: dict[str, Any]) -> tuple[int, int] | None:
        return HedgeExecutionOrchestrator._resolve_limit_bounds(summary)[0]

    @staticmethod
    def _resolve_limit_bounds(summary: dict[str, Any]) -> tuple[tuple[int, int] | None, str]:
        table_min = HedgeExecutionOrchestrator._parse_positive_int(summary.get("table_min"))
        table_max = HedgeExecutionOrchestrator._parse_positive_int(summary.get("table_max"))
        if table_min is not None and table_max is not None and table_max >= table_min:
            return (table_min, table_max), "table_min/table_max"

        for key in (
            "limit_label",
            "runtime_limit_label",
            "frontend_limit_label",
            "canvas_limit_label",
            "label_limit_label",
            "table_limit_label",
            "runtime_table_limit",
        ):
            limit_label = summary.get(key)
            parsed = HedgeExecutionOrchestrator._parse_limit_label(limit_label)
            if parsed is not None:
                return parsed, key
        return None, "missing"

    @staticmethod
    def _parse_limit_label(value: object) -> tuple[int, int] | None:
        text = str(value or "").strip()
        if not text:
            return None

        match = re.search(r"(\d+(?:\.\d+)?)\s*[-_/]\s*(\d+(?:\.\d+)?)", text)
        if not match:
            return None
        try:
            low = float(match.group(1))
            high = float(match.group(2))
        except ValueError:
            return None
        if low <= 0 or high <= 0 or low > high:
            return None
        return int(round(low)), int(round(high))

    @staticmethod
    def _parse_positive_int(value: object) -> int | None:
        if isinstance(value, bool):
            return None
        try:
            parsed = float(str(value).replace(",", "").strip())
            if parsed <= 0:
                return None
            return int(round(parsed))
        except Exception:
            return None

    @staticmethod
    def _parse_coordinate_readiness(summary: dict[str, Any]) -> tuple[bool, bool, bool]:
        ready, chips_ready, sides_ready, _source = HedgeExecutionOrchestrator._resolve_coordinate_readiness(summary)
        return ready, chips_ready, sides_ready

    @staticmethod
    def _resolve_coordinate_readiness(summary: dict[str, Any]) -> tuple[bool, bool, bool, str]:
        runtime_coordinates = summary.get("runtime_coordinates")
        runtime_map = isinstance(runtime_coordinates, dict)
        chip_regions = runtime_coordinates.get("chips") if runtime_map and isinstance(runtime_coordinates, dict) else None
        side_regions = runtime_coordinates.get("bet_regions") if runtime_map and isinstance(runtime_coordinates, dict) else None
        chip_regions_valid = bool(isinstance(chip_regions, dict) and chip_regions)
        side_regions_valid = bool(isinstance(side_regions, dict) and side_regions)
        runtime_coordinates_ready = runtime_map and chip_regions_valid and side_regions_valid

        coordinate_ready = HedgeExecutionOrchestrator._coerce_bool_or_none(summary.get("coordinate_ready"))
        chip_coordinate_ready = HedgeExecutionOrchestrator._coerce_bool_or_none(summary.get("chip_coordinate_ready"))
        if chip_coordinate_ready is None:
            chip_coordinate_ready = HedgeExecutionOrchestrator._coerce_bool_or_none(summary.get("chip_coordinates_ready"))
        side_coordinate_ready = HedgeExecutionOrchestrator._coerce_bool_or_none(summary.get("side_coordinate_ready"))
        if side_coordinate_ready is None:
            side_coordinate_ready = HedgeExecutionOrchestrator._coerce_bool_or_none(summary.get("side_coordinates_ready"))

        coordinate_explicit = coordinate_ready is not None
        chip_coordinate_explicit = chip_coordinate_ready is not None
        side_coordinate_explicit = side_coordinate_ready is not None
        source = "runtime_coordinates" if runtime_coordinates_ready else "summary_flags"
        if coordinate_ready is None:
            coordinate_ready = runtime_coordinates_ready
        if chip_coordinate_ready is None:
            chip_coordinate_ready = runtime_map and chip_regions_valid
        if side_coordinate_ready is None:
            side_coordinate_ready = runtime_map and side_regions_valid

        used_known_profile = False
        if (
            not all((coordinate_ready, chip_coordinate_ready, side_coordinate_ready))
            and HedgeExecutionOrchestrator._known_standard_coordinate_profile_available(summary)
        ):
            if not coordinate_explicit:
                coordinate_ready = True
                used_known_profile = True
            if not chip_coordinate_explicit:
                chip_coordinate_ready = True
                used_known_profile = True
            if not side_coordinate_explicit:
                side_coordinate_ready = True
                used_known_profile = True
        if used_known_profile:
            source = "known_960x620_profile"

        return bool(coordinate_ready), bool(chip_coordinate_ready), bool(side_coordinate_ready), source

    @staticmethod
    def _known_standard_coordinate_profile_available(summary: dict[str, Any]) -> bool:
        chip_regions = {key for key in LIVE_GAME_REGIONS if str(key).startswith("chip_")}
        side_regions = {"bet_player", "bet_banker", "bet_tie"}
        if not chip_regions or not side_regions.issubset(LIVE_GAME_REGIONS.keys()):
            return False

        viewport = summary.get("runtime_viewport")
        if isinstance(viewport, dict):
            width = HedgeExecutionOrchestrator._parse_positive_int(viewport.get("width"))
            height = HedgeExecutionOrchestrator._parse_positive_int(viewport.get("height"))
            return (width, height) == LIVE_GAME_BASE_SIZE

        return bool(summary.get("runtime_v2_shadow_bridge") or summary.get("runtime_v2_shadow_online"))

    @staticmethod
    def _coerce_bool_or_none(value: object) -> bool | None:
        if isinstance(value, bool):
            return value
        if value in ("true", "True", "1", 1):
            return True
        if value in ("false", "False", "0", 0):
            return False
        return None

    def _resolve_hedge_sides(self, model: str) -> tuple[str, str]:
        text = str(model or "").lower()
        has_banker = "庄" in text or "banker" in text
        has_player = "闲" in text or "player" in text

        if has_banker and not has_player:
            return "庄", "闲"
        if has_player and not has_banker:
            return "闲", "庄"
        return self._rng.choice([("庄", "闲"), ("闲", "庄")])

    @staticmethod
    def _extract_round_id(snapshot: TemporalStateSnapshot) -> str:
        summary = snapshot.safe_summary or {}
        candidate = str(summary.get("round_id", ""))
        if candidate:
            return candidate
        if snapshot.batch_id:
            return snapshot.batch_id
        return ""

    @staticmethod
    def _normalize_round_group(value: object) -> str:
        text = str(value or "").strip()
        if text in ("", "-"):
            return ""
        parts = text.split("-")
        if len(parts) >= 3 and all(part.isdigit() for part in parts[:3]):
            return parts[2]
        return text

    @staticmethod
    def _snapshot_execution_online(snapshot: TemporalStateSnapshot, *, current_ms: int) -> bool:
        if snapshot.ws_connected and snapshot.page_alive:
            return True
        summary = snapshot.safe_summary or {}
        if (
            snapshot.source == "runtime_v2_shadow_stable"
            and summary.get("runtime_v2_shadow_bridge") is True
            and max(0, current_ms - int(snapshot.timestamp_captured_ms)) <= MAX_SYNC_SNAPSHOT_AGE_MS
        ):
            return True
        return False

    @staticmethod
    def _extract_room_key(snapshot: TemporalStateSnapshot) -> str:
        summary = snapshot.safe_summary or {}
        for key in (
            "locked_room_label",
            "room_label",
            "frontend_room_label",
            "canvas_room_label",
            "runtime_room_label",
            "label_room_label",
            "table_id",
            "locked_room_id",
            "room_id",
            "frontend_room_id",
            "runtime_room_id",
        ):
            value = str(summary.get(key, "") or "").strip()
            if value and value != "-":
                return value
        return ""

    @staticmethod
    def _extract_phase_key(snapshot: TemporalStateSnapshot) -> str:
        summary = snapshot.safe_summary or {}
        for key in ("phase_key", "runtime_phase", "phase_text", "frontend_phase_text", "label_phase_text", "canvas_phase_text"):
            value = str(summary.get(key, "") or "").strip().lower()
            if value and value not in {"-", "unknown"}:
                return value
        return ""

    @staticmethod
    def _parse_balance(value: str) -> float:
        try:
            return float(str(value).replace(",", "").strip() or 0.0)
        except ValueError:
            return 0.0

    def _emit(self, event_type: ExecutionEventType | str, *, state: str, message: str, safe_summary: dict[str, Any] | None = None) -> None:
        event = ExecutionEvent(
            event_type=event_type,
            state=state,
            message=message,
            safe_summary=safe_summary or {},
        )
        self._events.append(event)
        if self._event_listener is not None:
            self._event_listener(event)


__all__ = [
    "HedgeExecutionOrchestrator",
    "HedgeExecutionState",
    "HedgeShadowPlan",
]

