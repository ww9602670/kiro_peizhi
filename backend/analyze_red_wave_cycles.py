from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from math import ceil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.utils.key_code_map import check_win


DEFAULT_DB = Path(__file__).resolve().parent.parent / "jnd28.sqlite3"
DEFAULT_START_ISSUE = 3402402
DEFAULT_END_ISSUE = 3414863
DEFAULT_CODES = ("B1LM_S", "B2LM_S", "B3LM_S", "DS4")
RED_BALL = {3, 6, 9}
RED_SUM = {3, 6, 9, 12, 15, 18, 21, 24}


@dataclass
class CycleStats:
    dist: Counter
    bust: int
    cycles: int
    bucket: dict[str, Counter]
    bucket_cycles: Counter
    profits: list[float]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze red-wave cycle distribution.")
    parser.add_argument(
        "--db",
        default=str(DEFAULT_DB),
        help="Path to jnd28 sqlite db",
    )
    parser.add_argument(
        "--start-issue",
        type=int,
        default=DEFAULT_START_ISSUE,
        help="Inclusive start issue",
    )
    parser.add_argument(
        "--end-issue",
        type=int,
        default=DEFAULT_END_ISSUE,
        help="Inclusive end issue",
    )
    parser.add_argument(
        "--sequence",
        default="1,3,9,27,81,243,729",
        help="Comma-separated unit sequence",
    )
    parser.add_argument(
        "--odds",
        type=float,
        default=1.9834,
        help="Decimal odds used for profit math",
    )
    return parser.parse_args()


def parse_sequence(raw: str) -> list[int]:
    values = [int(part.strip()) for part in raw.split(",") if part.strip()]
    if not values:
        raise ValueError("sequence is required")
    if any(v <= 0 for v in values):
        raise ValueError("sequence values must be > 0")
    return values


def build_bucket_plan(period_days: list[sqlite3.Row]) -> tuple[list[str], dict[str, str]]:
    days = [row["d"] for row in period_days]
    if not days:
        return [], {}

    size_base = len(days) // 3
    remainder = len(days) % 3
    sizes = [size_base + (1 if idx < remainder else 0) for idx in range(3)]

    order: list[str] = []
    day_to_bucket: dict[str, str] = {}
    cursor = 0
    for size in sizes:
        if size <= 0:
            continue
        chunk = days[cursor : cursor + size]
        cursor += size
        label = f"{chunk[0][5:]}~{chunk[-1][5:]}"
        order.append(label)
        for day in chunk:
            day_to_bucket[day] = label

    return order, day_to_bucket


def is_red_trigger(code: str, row: sqlite3.Row) -> bool:
    if code == "B1LM_S":
        return row["d1"] in RED_BALL
    if code == "B2LM_S":
        return row["d2"] in RED_BALL
    if code == "B3LM_S":
        return row["d3"] in RED_BALL
    if code == "DS4":
        return row["sum"] in RED_SUM
    raise ValueError(f"unsupported code: {code}")


def load_rows(db_path: str, start_issue: int, end_issue: int) -> tuple[list[sqlite3.Row], list[sqlite3.Row]]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT issue, open_time, d1, d2, d3, sum "
            "FROM jnd28_history ORDER BY CAST(issue AS INTEGER) ASC"
        ).fetchall()
        period_days = conn.execute(
            """
            SELECT substr(open_time,1,10) AS d, MIN(CAST(issue AS INTEGER)) AS start_issue
            FROM jnd28_history
            WHERE CAST(issue AS INTEGER) >= ? AND CAST(issue AS INTEGER) <= ? AND open_time IS NOT NULL
            GROUP BY substr(open_time,1,10)
            ORDER BY d
            """,
            (start_issue, end_issue),
        ).fetchall()
        return rows, period_days
    finally:
        conn.close()


def make_issue_to_day(period_days: list[sqlite3.Row], end_issue: int):
    def issue_to_day(issue_int: int) -> str | None:
        for idx, row in enumerate(period_days):
            start = row["start_issue"]
            end = period_days[idx + 1]["start_issue"] - 1 if idx + 1 < len(period_days) else end_issue
            if start <= issue_int <= end:
                return row["d"]
        return None

    return issue_to_day


def build_profit_table(sequence: list[int], odds: float) -> tuple[float, dict[int, float]]:
    burst_loss = float(sum(sequence))
    cumulative = 0.0
    profit_by_attempt: dict[int, float] = {}
    for attempt, stake in enumerate(sequence, start=1):
        profit_by_attempt[attempt] = stake * (odds - 1) - cumulative
        cumulative += stake
    return burst_loss, profit_by_attempt


def analyze(
    rows: list[sqlite3.Row],
    period_days: list[sqlite3.Row],
    start_issue: int,
    end_issue: int,
    sequence: list[int],
    odds: float,
) -> tuple[dict[str, CycleStats], CycleStats, float, dict[int, float], list[str]]:
    issue_to_day = make_issue_to_day(period_days, end_issue)
    bucket_order, day_to_bucket = build_bucket_plan(period_days)
    burst_loss, profit_by_attempt = build_profit_table(sequence, odds)
    max_attempt = len(sequence)
    stats = {
        code: CycleStats(
            dist=Counter(),
            bust=0,
            cycles=0,
            bucket=defaultdict(Counter),
            bucket_cycles=Counter(),
            profits=[],
        )
        for code in DEFAULT_CODES
    }

    for code in DEFAULT_CODES:
        active = False
        attempt = 0
        cycle_start_issue: int | None = None
        cycle_start_day: str | None = None
        prev: sqlite3.Row | None = None

        for row in rows:
            issue_int = int(row["issue"])
            placed = False

            if prev is not None:
                if active:
                    placed = True
                elif is_red_trigger(code, prev):
                    active = True
                    attempt = 1
                    cycle_start_issue = issue_int
                    cycle_start_day = issue_to_day(issue_int)
                    placed = True

            if placed:
                win = check_win(code, [row["d1"], row["d2"], row["d3"]], row["sum"])
                if cycle_start_issue is not None and start_issue <= cycle_start_issue <= end_issue:
                    bucket = day_to_bucket.get(cycle_start_day) if cycle_start_day is not None else None
                    if win:
                        stats[code].dist[attempt] += 1
                        stats[code].cycles += 1
                        if bucket is not None:
                            stats[code].bucket[bucket][attempt] += 1
                            stats[code].bucket_cycles[bucket] += 1
                        stats[code].profits.append(profit_by_attempt[attempt])
                    elif attempt == max_attempt:
                        stats[code].bust += 1
                        stats[code].cycles += 1
                        if bucket is not None:
                            stats[code].bucket[bucket]["bust"] += 1
                            stats[code].bucket_cycles[bucket] += 1
                        stats[code].profits.append(-burst_loss)

                if win:
                    active = False
                    attempt = 0
                    cycle_start_issue = None
                    cycle_start_day = None
                elif attempt == max_attempt:
                    active = False
                    attempt = 0
                    cycle_start_issue = None
                    cycle_start_day = None
                else:
                    attempt += 1

            prev = row

    combined = CycleStats(
        dist=Counter(),
        bust=0,
        cycles=0,
        bucket=defaultdict(Counter),
        bucket_cycles=Counter(),
        profits=[],
    )
    for code in DEFAULT_CODES:
        combined.dist.update(stats[code].dist)
        combined.bust += stats[code].bust
        combined.cycles += stats[code].cycles
        combined.profits.extend(stats[code].profits)
        for bucket, counter in stats[code].bucket.items():
            combined.bucket[bucket].update(counter)
        combined.bucket_cycles.update(stats[code].bucket_cycles)

    return stats, combined, burst_loss, profit_by_attempt, bucket_order


def print_report(
    stats: dict[str, CycleStats],
    combined: CycleStats,
    burst_loss: float,
    profit_by_attempt: dict[int, float],
    sequence: list[int],
    bucket_order: list[str],
) -> None:
    print("sequence", sequence)
    print("burst_loss_units", round(burst_loss, 4))
    for attempt in range(1, len(sequence) + 1):
        profit = profit_by_attempt[attempt]
        recovery = ceil(burst_loss / profit) if profit > 0 else "inf"
        print(
            "attempt",
            attempt,
            "profit_units",
            round(profit, 4),
            "wins_needed_for_burst_recovery",
            recovery,
        )
    print("---")

    named_sets: list[tuple[str, CycleStats]] = [("ALL", combined)] + [(code, stats[code]) for code in DEFAULT_CODES]
    for name, data in named_sets:
        print("name", name)
        print("cycles", data.cycles, "bust", data.bust)
        for attempt in range(1, len(sequence) + 1):
            count = data.dist[attempt]
            ratio = round(count / data.cycles, 6) if data.cycles else 0
            print(f"p{attempt}", count, ratio)
        bust_ratio = round(data.bust / data.cycles, 6) if data.cycles else 0
        avg_profit = sum(data.profits) / len(data.profits) if data.profits else 0
        total_bets = sum(attempt * count for attempt, count in data.dist.items()) + len(sequence) * data.bust
        avg_bets_per_cycle = total_bets / data.cycles if data.cycles else 0

        print("bust_ratio", bust_ratio)
        print("avg_cycle_profit_units", round(avg_profit, 4))
        print("avg_bets_per_cycle", round(avg_bets_per_cycle, 4))
        if avg_profit > 0:
            cycles_needed = ceil(burst_loss / avg_profit)
            bets_needed = ceil(cycles_needed * avg_bets_per_cycle)
            print("avg_cycles_needed_to_recover_one_burst", cycles_needed)
            print("avg_bets_needed_to_recover_one_burst", bets_needed)
        else:
            print("avg_cycles_needed_to_recover_one_burst", "inf")
            print("avg_bets_needed_to_recover_one_burst", "inf")

        print("bucket")
        for bucket in bucket_order:
            bucket_cycles = data.bucket_cycles[bucket]
            if not bucket_cycles:
                print(bucket, "0")
                continue
            parts = [bucket, str(bucket_cycles)]
            for attempt in range(1, len(sequence) + 1):
                parts.append(f"p{attempt}={data.bucket[bucket][attempt] / bucket_cycles:.4f}")
            parts.append(f"bust={data.bucket[bucket]['bust'] / bucket_cycles:.4f}")
            print(" ".join(parts))
        print("---")


def main() -> None:
    args = parse_args()
    sequence = parse_sequence(args.sequence)
    rows, period_days = load_rows(args.db, args.start_issue, args.end_issue)
    stats, combined, burst_loss, profit_by_attempt, bucket_order = analyze(
        rows=rows,
        period_days=period_days,
        start_issue=args.start_issue,
        end_issue=args.end_issue,
        sequence=sequence,
        odds=args.odds,
    )
    print_report(
        stats=stats,
        combined=combined,
        burst_loss=burst_loss,
        profit_by_attempt=profit_by_attempt,
        sequence=sequence,
        bucket_order=bucket_order,
    )


if __name__ == "__main__":
    main()
