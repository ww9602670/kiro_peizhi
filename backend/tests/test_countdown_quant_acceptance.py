from __future__ import annotations

import json
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from probe_countdown_quant_acceptance import load_events, main, summarize_events


def _write_log(path: Path, lines: list[str]) -> Path:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


class TestCountdownQuantAcceptance:
    def test_load_events_and_summary_counts(self, tmp_path: Path):
        log_path = _write_log(
            tmp_path / "countdown.log",
            [
                "plain text line",
                json.dumps(
                    {
                        "timestamp": "2026-04-17T01:00:00+00:00",
                        "action": "countdown_validation",
                        "phase": "pre_submit",
                        "allowed": True,
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "timestamp": "2026-04-17T01:00:02+00:00",
                        "action": "bet",
                        "key_code": "CONFIRMBET_BATCH",
                        "terminal": True,
                        "result": "confirmbet_success",
                        "error_code": "SUCCESS",
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "timestamp": "2026-04-17T01:05:00+00:00",
                        "action": "countdown_validation",
                        "phase": "retry_submit",
                        "allowed": False,
                    },
                    ensure_ascii=False,
                ),
            ],
        )

        events = load_events([log_path])
        report = summarize_events(
            events,
            min_opportunities_per_day=1,
            baseline_rate=0.5,
            target_reduction_pct=80.0,
        )

        assert len(events) == 3
        assert report["summary"]["total_opportunities"] == 1
        assert report["summary"]["successes"] == 1
        assert report["summary"]["eligible_terminal_batches"] == 1
        assert report["summary"]["current_odds_changed_error_rate"] == 0.0
        assert report["summary"]["reduction_pass"] is True
        assert report["daily"][0]["retry_revalidations"] == 1

    def test_main_writes_report_files(self, tmp_path: Path, capsys):
        log_path = _write_log(
            tmp_path / "multi_day.log",
            [
                json.dumps(
                    {
                        "timestamp": "2026-04-14T01:00:00+00:00",
                        "action": "countdown_validation",
                        "phase": "pre_submit",
                        "allowed": True,
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "timestamp": "2026-04-14T01:00:05+00:00",
                        "action": "bet",
                        "key_code": "CONFIRMBET_BATCH",
                        "terminal": True,
                        "result": "confirmbet_success",
                        "error_code": "SUCCESS",
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "timestamp": "2026-04-15T01:00:00+00:00",
                        "action": "countdown_validation",
                        "phase": "pre_submit",
                        "allowed": True,
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "timestamp": "2026-04-15T01:00:05+00:00",
                        "action": "bet",
                        "key_code": "CONFIRMBET_BATCH",
                        "terminal": True,
                        "result": "confirmbet_success",
                        "error_code": "SUCCESS",
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "timestamp": "2026-04-16T01:00:00+00:00",
                        "action": "countdown_validation",
                        "phase": "pre_submit",
                        "allowed": True,
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "timestamp": "2026-04-16T01:00:05+00:00",
                        "action": "bet",
                        "key_code": "CONFIRMBET_BATCH",
                        "terminal": True,
                        "result": "confirmbet_success",
                        "error_code": "SUCCESS",
                    },
                    ensure_ascii=False,
                ),
            ],
        )

        out_root = tmp_path / "reports"
        exit_code = main(
            [
                str(log_path),
                "--out-root",
                str(out_root),
                "--baseline-rate",
                "1.0",
                "--min-opportunities-per-day",
                "1",
            ]
        )

        assert exit_code == 0
        printed = capsys.readouterr().out.strip().splitlines()
        report_path = Path(printed[0])
        markdown_path = Path(printed[1])
        assert report_path.exists()
        assert markdown_path.exists()

        report = json.loads(report_path.read_text(encoding="utf-8"))
        assert report["summary"]["days_meeting_min_volume"] == 3
        assert report["summary"]["volume_pass"] is True
        assert report["summary"]["acceptance_ready"] is True
