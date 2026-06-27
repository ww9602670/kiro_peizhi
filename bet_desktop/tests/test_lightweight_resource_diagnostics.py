from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "lightweight_resource_diagnostics.py"
SPEC = importlib.util.spec_from_file_location("lightweight_resource_diagnostics", SCRIPT)
assert SPEC and SPEC.loader
diagnostics = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = diagnostics
SPEC.loader.exec_module(diagnostics)


def test_capacity_uses_three_limits_and_safety_factor() -> None:
    result = diagnostics.estimate_capacity(
        free_memory_mb=16000,
        suite_memory_mb=2000,
        suite_cpu_percent=10,
        suite_browser_processes=8,
        browser_process_limit=80,
    )

    assert result["memory_sets"] == 5
    assert result["cpu_sets"] == 7
    assert result["browser_sets"] == 10
    assert result["final_recommendation"] == 3
    assert result["missing_inputs"] == []


def test_capacity_does_not_invent_browser_count_when_missing() -> None:
    result = diagnostics.estimate_capacity(
        free_memory_mb=16000,
        suite_memory_mb=1000,
        suite_cpu_percent=0,
        suite_browser_processes=0,
        browser_process_limit=80,
    )

    assert result["memory_sets"] == 11
    assert result["cpu_sets"] is None
    assert result["browser_sets"] is None
    assert result["final_recommendation"] is None
    assert result["temporary_recommendation"] == 7
    assert "单套平均CPU百分比" in result["missing_inputs"]
    assert "单套浏览器进程数" in result["missing_inputs"]


def test_report_marks_manual_items_and_one_time_scope() -> None:
    zero = diagnostics.GroupStats(0, 0.0, 0.0, 0.0)
    data = {
        "generated_at": "2026-06-27 12:00:00",
        "mode": "idle-exe-smoke",
        "duration_seconds": 16.0,
        "exe_path": "H:/d/bocai_web/dist/LightweightHedgeConsole/LightweightHedgeConsole.exe",
        "browser_process_limit": 80,
        "system": {"total_memory_mb": 32000.0, "free_memory_mb": 16000.0},
        "system_cpu_avg_percent": 1.0,
        "system_cpu_peak_percent": 2.0,
        "baseline": {"python_ui": zero, "chromium": zero},
        "smoke": {"python_ui": zero, "chromium": zero, "suite": zero},
        "capacity": diagnostics.estimate_capacity(16000, 0, 0, 0, 80),
    }

    report = diagnostics.render_report(data)

    assert "最终人工 exe 测试时采集" in report
    assert "未登录、未进房、未启动下注控制" in report
    assert "未新增常驻资源监控线程" in report
