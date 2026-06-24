#!/usr/bin/env python
"""Read-only monitor for live execution audit logs.

It groups real execution records by round and account:
- plan amount from [真实计划]
- dispatched amount from [真实下注动作]
- confirmed real bet amount from [真实置信日志] 下注确认
- settlement P/L from [真实置信日志] 本局结算
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable


ACCOUNTS = ("a1", "a2", "a3", "a4")

RE_START = re.compile(r"\[真实执行\]\s*启动|已启动真实执行")
RE_STOP = re.compile(r"\[真实执行\]\s*停止|请求停止")
RE_PLAN = re.compile(r"\[真实计划\]\s*局号=([^ |]+)\s*\|\s*(.+)")
RE_PLAN_LEG = re.compile(r"(a[1-4])\s+([庄闲和])\s+(-?\d+(?:\.\d+)?)")
RE_DISPATCH_OK = re.compile(r"\[真实下注动作\]\s*单账号(?:计划已下发|完成)\s*\|\s*局号=([^ |]+)\s*\|\s*(a[1-4])\s+([庄闲和])\s+(-?\d+(?:\.\d+)?)")
RE_DISPATCH_FAIL = re.compile(r"\[真实下注动作\]\s*单账号下发失败\s*\|\s*局号=([^ |]+)\s*\|\s*(a[1-4])\s+([庄闲和])\s+(-?\d+(?:\.\d+)?)\s*\|\s*原因=(.+)")
RE_CONFIRM = re.compile(r"下注确认\s*\|\s*(a[1-4])\s+下注确认:\s*局=([^\s]+)\s+金额=(-?\d+(?:\.\d+)?)\s+下注前=(-?\d+(?:\.\d+)?)\s+期望下注后=(-?\d+(?:\.\d+)?)")
RE_BALANCE_CHECK = re.compile(r"余额核对\s*\|\s*(a[1-4])\s+下注后余额已核对:\s*局=([^\s]+)\s+余额=(-?\d+(?:\.\d+)?)")
RE_SETTLE = re.compile(r"本局结算\s*\|\s*(a[1-4])\s+本局结算:\s*局=([^\s]+)\s+结算后=(-?\d+(?:\.\d+)?)\s+盈亏=(-?\d+(?:\.\d+)?)")


@dataclass
class AccountRound:
    plan_side: str = ""
    plan_amount: float = 0.0
    dispatch_amount: float = 0.0
    dispatch_failed: str = ""
    confirm_amount: float = 0.0
    confirm_count: int = 0
    confirm_keys: set[tuple[str, str, str, str]] = field(default_factory=set)
    first_before: float | None = None
    last_expected_after: float | None = None
    balance_checked: float | None = None
    settle_after: float | None = None
    pnl: float | None = None
    local_round_ids: set[str] = field(default_factory=set)


@dataclass
class Round:
    batch_id: str
    ts: int
    text: str
    accounts: dict[str, AccountRound] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def planned_accounts(self) -> list[str]:
        return [a for a in ACCOUNTS if a in self.accounts and self.accounts[a].plan_amount > 0]


def latest_audit_log(root: Path) -> Path:
    logs = sorted(
        (root / "live_logs").glob("ui_execution_audit_*.jsonl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not logs:
        raise FileNotFoundError("No ui_execution_audit_*.jsonl found under live_logs")
    return logs[0]


def read_events(path: Path) -> list[dict]:
    events: list[dict] = []
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            raw = str(obj.get("raw") or obj.get("display") or "")
            ts = int(obj.get("timestamp_ms") or 0)
            events.append({"ts": ts, "raw": raw})
    return events


def fmt_ts(ts: int) -> str:
    if not ts:
        return "-"
    return datetime.fromtimestamp(ts / 1000).strftime("%H:%M:%S")


def amount_text(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.2f}"


def last_start_ts(events: Iterable[dict]) -> int:
    start = 0
    for ev in events:
        if RE_START.search(ev["raw"]):
            start = ev["ts"]
    return start


def find_round_for_event(
    rounds: list[Round],
    ts: int,
    account: str,
    local_id: str | None = None,
    max_age_ms: int | None = None,
) -> Round | None:
    # Prefer the latest planned round before this event for the same account.
    candidates = [r for r in rounds if r.ts <= ts and account in r.accounts]
    if max_age_ms is not None:
        candidates = [r for r in candidates if ts - r.ts <= max_age_ms]
    if not candidates:
        return None

    if local_id:
        with_same_local = [r for r in candidates if local_id in r.accounts[account].local_round_ids]
        if with_same_local:
            return with_same_local[-1]

    # Avoid attaching a late event to a round that is already settled for this account.
    unsettled = [r for r in candidates if r.accounts[account].pnl is None]
    if unsettled:
        return unsettled[-1]
    return candidates[-1]


def parse_rounds(events: list[dict], since_ts: int = 0) -> tuple[list[Round], list[dict]]:
    rounds: list[Round] = []
    by_batch: dict[str, Round] = {}
    scoped = [ev for ev in events if ev["ts"] >= since_ts]

    for ev in scoped:
        raw = ev["raw"]

        m = RE_PLAN.search(raw)
        if m:
            batch_id = m.group(1)
            round_obj = Round(batch_id=batch_id, ts=ev["ts"], text=raw)
            for acct, side, amt in RE_PLAN_LEG.findall(m.group(2)):
                ar = round_obj.accounts.setdefault(acct, AccountRound())
                ar.plan_side = side
                ar.plan_amount += float(amt)
            rounds.append(round_obj)
            by_batch[batch_id] = round_obj
            continue

        m = RE_DISPATCH_OK.search(raw)
        if m:
            batch_id, acct, _side, amt = m.groups()
            r = by_batch.get(batch_id)
            if r and acct in r.accounts:
                r.accounts[acct].dispatch_amount += float(amt)
            continue

        m = RE_DISPATCH_FAIL.search(raw)
        if m:
            batch_id, acct, _side, _amt, reason = m.groups()
            r = by_batch.get(batch_id)
            if r:
                ar = r.accounts.setdefault(acct, AccountRound())
                ar.dispatch_failed = reason
            continue

        m = RE_CONFIRM.search(raw)
        if m:
            acct, local_id, amount, before, expected_after = m.groups()
            r = find_round_for_event(rounds, ev["ts"], acct, local_id, max_age_ms=30_000)
            if r:
                ar = r.accounts[acct]
                ar.local_round_ids.add(local_id)
                confirm_key = (local_id, amount, before, expected_after)
                if confirm_key not in ar.confirm_keys:
                    ar.confirm_keys.add(confirm_key)
                    ar.confirm_amount += float(amount)
                    ar.confirm_count += 1
                if ar.first_before is None:
                    ar.first_before = float(before)
                ar.last_expected_after = float(expected_after)
            continue

        m = RE_BALANCE_CHECK.search(raw)
        if m:
            acct, local_id, balance = m.groups()
            r = find_round_for_event(rounds, ev["ts"], acct, local_id, max_age_ms=30_000)
            if r:
                ar = r.accounts[acct]
                ar.local_round_ids.add(local_id)
                ar.balance_checked = float(balance)
            continue

        m = RE_SETTLE.search(raw)
        if m:
            acct, local_id, after, pnl = m.groups()
            r = find_round_for_event(rounds, ev["ts"], acct, local_id, max_age_ms=90_000)
            if r:
                ar = r.accounts[acct]
                ar.local_round_ids.add(local_id)
                ar.settle_after = float(after)
                ar.pnl = float(pnl)
            continue

    return rounds, scoped


def classify_round(r: Round) -> tuple[str, list[str]]:
    planned = r.planned_accounts
    if not planned:
        return "NO_PLAN", ["no planned account parsed"]

    issues: list[str] = []
    missing_dispatch: list[str] = []
    missing_confirm: list[str] = []
    mismatch_confirm: list[str] = []
    missing_settle: list[str] = []

    for acct in planned:
        ar = r.accounts[acct]
        if ar.dispatch_failed:
            issues.append(f"{acct}下发失败:{ar.dispatch_failed}")
        if round(ar.dispatch_amount, 2) != round(ar.plan_amount, 2):
            missing_dispatch.append(acct)
        if ar.confirm_amount <= 0:
            missing_confirm.append(acct)
        elif round(ar.confirm_amount, 2) != round(ar.plan_amount, 2):
            mismatch_confirm.append(f"{acct}确认{ar.confirm_amount:.2f}/计划{ar.plan_amount:.2f}")
        if ar.pnl is None:
            missing_settle.append(acct)

    if len(missing_confirm) == len(planned):
        issues.append("整组真实下注确认缺失")
    elif missing_confirm:
        issues.append("单平台真实下注确认缺失:" + ",".join(missing_confirm))
    if mismatch_confirm:
        issues.append("真实确认金额不一致:" + ",".join(mismatch_confirm))
    if missing_dispatch:
        issues.append("下发记录不完整:" + ",".join(missing_dispatch))
    if missing_settle:
        issues.append("结算记录缺失:" + ",".join(missing_settle))

    if issues:
        return "WARN", issues
    return "OK", []


def print_report(path: Path, rounds: list[Round], events: list[dict], last_n: int) -> int:
    shown = rounds[-last_n:] if last_n > 0 else rounds
    open_rounds = 0
    warn_rounds = 0

    print(f"log={path}")
    if events:
        print(f"window={fmt_ts(events[0]['ts'])}..{fmt_ts(events[-1]['ts'])} rounds={len(rounds)} shown={len(shown)}")
    else:
        print("window=- rounds=0 shown=0")

    for r in shown:
        status, issues = classify_round(r)
        if status != "OK":
            warn_rounds += 1
        if any(r.accounts[a].pnl is None for a in r.planned_accounts):
            open_rounds += 1

        planned_parts = []
        for acct in r.planned_accounts:
            ar = r.accounts[acct]
            planned_parts.append(
                f"{acct}{ar.plan_side} plan={ar.plan_amount:.2f} "
                f"dispatch={ar.dispatch_amount:.2f} actual={ar.confirm_amount:.2f} "
                f"pnl={amount_text(ar.pnl)} local={','.join(sorted(ar.local_round_ids)) or '-'}"
            )
        pnl_sum = sum((r.accounts[a].pnl or 0.0) for a in r.planned_accounts)
        print(f"{fmt_ts(r.ts)} {status} {r.batch_id} pnl_sum={pnl_sum:.2f}")
        print("  " + " | ".join(planned_parts))
        if issues:
            print("  issue=" + "；".join(issues))

    print(f"summary warn_rounds={warn_rounds} open_rounds={open_rounds}")
    return 1 if warn_rounds else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Monitor live execution audit records.")
    parser.add_argument("--log", type=Path, default=None, help="Path to ui_execution_audit_*.jsonl")
    parser.add_argument("--since-last-start", action="store_true", default=True, help="Only analyze records after the latest real execution start")
    parser.add_argument("--all", action="store_true", help="Analyze the whole file instead of only the latest start")
    parser.add_argument("--last", type=int, default=20, help="Number of latest rounds to print")
    parser.add_argument("--watch-seconds", type=int, default=0, help="Repeat analysis for N seconds")
    parser.add_argument("--interval", type=int, default=10, help="Watch interval in seconds")
    args = parser.parse_args()

    root = Path.cwd()
    path = args.log or latest_audit_log(root)

    end_at = time.time() + args.watch_seconds if args.watch_seconds > 0 else time.time()
    exit_code = 0
    first = True
    while first or time.time() < end_at:
        first = False
        events = read_events(path)
        since = 0 if args.all else last_start_ts(events)
        rounds, scoped = parse_rounds(events, since)
        exit_code = print_report(path, rounds, scoped, args.last)
        if time.time() >= end_at:
            break
        print("-" * 80)
        sys.stdout.flush()
        time.sleep(max(1, args.interval))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
