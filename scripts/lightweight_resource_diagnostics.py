from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXE = PROJECT_ROOT / "dist" / "LightweightHedgeConsole" / "LightweightHedgeConsole.exe"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "artifacts" / "resource-diagnostics"
PYTHON_UI_NAMES = {"python.exe", "pythonw.exe", "lightweighthedgeconsole.exe"}
CHROMIUM_NAMES = {"chrome.exe", "chromium.exe"}
MANUAL_RESERVED_NOTE = "未采集：最终人工 exe 测试时采集"


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    ppid: int
    name: str
    memory_mb: float
    cpu_seconds: float
    path: str = ""
    command_line: str = ""


@dataclass(frozen=True)
class GroupStats:
    count: int
    memory_mb: float
    avg_cpu_percent: float
    peak_cpu_percent: float


def _run_powershell_json(script: str) -> Any:
    completed = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    text = completed.stdout.strip()
    if not text:
        return None
    return json.loads(text)


def read_system_snapshot() -> dict[str, float]:
    script = r"""
$os = Get-CimInstance Win32_OperatingSystem
[pscustomobject]@{
  TotalMemoryMB = [math]::Round($os.TotalVisibleMemorySize / 1024, 2)
  FreeMemoryMB = [math]::Round($os.FreePhysicalMemory / 1024, 2)
  ProcessorCount = [Environment]::ProcessorCount
} | ConvertTo-Json -Compress
"""
    data = _run_powershell_json(script)
    return {
        "total_memory_mb": float(data.get("TotalMemoryMB", 0) or 0),
        "free_memory_mb": float(data.get("FreeMemoryMB", 0) or 0),
        "processor_count": float(data.get("ProcessorCount", os.cpu_count() or 1) or 1),
    }


def read_process_snapshot() -> dict[int, ProcessInfo]:
    script = r"""
Get-CimInstance Win32_Process |
  Select-Object ProcessId,ParentProcessId,Name,WorkingSetSize,KernelModeTime,UserModeTime,ExecutablePath,CommandLine |
  ConvertTo-Json -Compress
"""
    raw = _run_powershell_json(script)
    if raw is None:
        return {}
    rows = raw if isinstance(raw, list) else [raw]
    processes: dict[int, ProcessInfo] = {}
    for row in rows:
        try:
            pid = int(row.get("ProcessId") or 0)
        except (TypeError, ValueError):
            continue
        if pid <= 0:
            continue
        kernel = _safe_float(row.get("KernelModeTime")) / 10_000_000
        user = _safe_float(row.get("UserModeTime")) / 10_000_000
        processes[pid] = ProcessInfo(
            pid=pid,
            ppid=int(row.get("ParentProcessId") or 0),
            name=str(row.get("Name") or "").lower(),
            memory_mb=_safe_float(row.get("WorkingSetSize")) / 1024 / 1024,
            cpu_seconds=kernel + user,
            path=str(row.get("ExecutablePath") or ""),
            command_line=str(row.get("CommandLine") or ""),
        )
    return processes


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def is_python_ui_process(process: ProcessInfo) -> bool:
    return process.name in PYTHON_UI_NAMES


def is_chromium_process(process: ProcessInfo) -> bool:
    return process.name in CHROMIUM_NAMES


def descendants_of(root_pid: int, processes: dict[int, ProcessInfo]) -> set[int]:
    children: dict[int, list[int]] = {}
    for process in processes.values():
        children.setdefault(process.ppid, []).append(process.pid)
    found: set[int] = set()
    stack = [root_pid]
    while stack:
        current = stack.pop()
        for child in children.get(current, []):
            if child not in found:
                found.add(child)
                stack.append(child)
    return found


def relevant_pids(processes: dict[int, ProcessInfo]) -> set[int]:
    return {
        pid
        for pid, process in processes.items()
        if is_python_ui_process(process) or is_chromium_process(process)
    }


def group_memory(processes: dict[int, ProcessInfo], pids: Iterable[int]) -> float:
    return sum(processes[pid].memory_mb for pid in pids if pid in processes)


def group_count(processes: dict[int, ProcessInfo], pids: Iterable[int]) -> int:
    return sum(1 for pid in pids if pid in processes)


def cpu_percent_between(
    before: dict[int, ProcessInfo],
    after: dict[int, ProcessInfo],
    pids: Iterable[int],
    elapsed_seconds: float,
    logical_cpus: float,
) -> float:
    if elapsed_seconds <= 0 or logical_cpus <= 0:
        return 0.0
    delta = 0.0
    for pid in pids:
        if pid in before and pid in after:
            delta += max(0.0, after[pid].cpu_seconds - before[pid].cpu_seconds)
    return max(0.0, (delta / elapsed_seconds / logical_cpus) * 100)


def summarize_group(
    samples: list[tuple[float, dict[int, ProcessInfo]]],
    pids_by_sample: list[set[int]],
    logical_cpus: float,
) -> GroupStats:
    if not samples:
        return GroupStats(0, 0.0, 0.0, 0.0)
    peak_count = 0
    peak_memory = 0.0
    cpu_values: list[float] = []
    for index, (_, processes) in enumerate(samples):
        pids = pids_by_sample[index]
        peak_count = max(peak_count, group_count(processes, pids))
        peak_memory = max(peak_memory, group_memory(processes, pids))
        if index == 0:
            continue
        previous_time, previous_processes = samples[index - 1]
        current_time, _ = samples[index]
        previous_pids = pids_by_sample[index - 1] | pids
        cpu_values.append(
            cpu_percent_between(
                previous_processes,
                processes,
                previous_pids,
                max(0.001, current_time - previous_time),
                logical_cpus,
            )
        )
    avg_cpu = sum(cpu_values) / len(cpu_values) if cpu_values else 0.0
    peak_cpu = max(cpu_values) if cpu_values else 0.0
    return GroupStats(peak_count, peak_memory, avg_cpu, peak_cpu)


def estimate_capacity(
    free_memory_mb: float,
    suite_memory_mb: float,
    suite_cpu_percent: float,
    suite_browser_processes: int,
    browser_process_limit: int,
) -> dict[str, Any]:
    memory_sets = math.floor((free_memory_mb * 0.7) / suite_memory_mb) if suite_memory_mb > 0 else None
    cpu_sets = math.floor(70 / suite_cpu_percent) if suite_cpu_percent > 0 else None
    browser_sets = (
        math.floor(browser_process_limit / suite_browser_processes)
        if suite_browser_processes > 0 and browser_process_limit > 0
        else None
    )
    known = [value for value in (memory_sets, cpu_sets, browser_sets) if value is not None]
    temporary_recommendation = math.floor(min(known) * 0.7) if known else None
    final_recommendation = temporary_recommendation if None not in (memory_sets, cpu_sets, browser_sets) else None
    return {
        "memory_sets": memory_sets,
        "cpu_sets": cpu_sets,
        "browser_sets": browser_sets,
        "temporary_recommendation": temporary_recommendation,
        "final_recommendation": final_recommendation,
        "missing_inputs": [
            name
            for name, value in (
                ("单套增量内存", memory_sets),
                ("单套平均CPU百分比", cpu_sets),
                ("单套浏览器进程数", browser_sets),
            )
            if value is None
        ],
    }


def _format_int_or_unknown(value: Any) -> str:
    return "无法估算" if value is None else str(value)


def render_report(data: dict[str, Any]) -> str:
    capacity = data["capacity"]
    smoke = data["smoke"]
    baseline = data["baseline"]
    manual_items = [
        "4 个账号登录后资源",
        "4 个账号进房后资源",
        "启动轻量控制后的资源",
        "真实平台页面长时间运行资源",
        "真实下注闭环中的 CPU、内存、浏览器进程变化",
    ]
    lines = [
        "# 阶段11一次性资源诊断报告",
        "",
        f"- 生成时间：{data['generated_at']}",
        f"- 采样模式：{data['mode']}",
        f"- 采样窗口：{data['duration_seconds']:.1f} 秒",
        f"- 是否触碰保护链路：否，仅启动/关闭空闲窗口；未登录、未进房、未启动下注控制、未使用真实账号动作。",
        f"- exe 路径：{data['exe_path']}",
        "",
        "## 自动已采集",
        "",
        f"- 系统内存：总量 {data['system']['total_memory_mb']:.1f} MB，可用 {data['system']['free_memory_mb']:.1f} MB。",
        f"- 采样期间系统 CPU：平均 {data['system_cpu_avg_percent']:.2f}%，峰值 {data['system_cpu_peak_percent']:.2f}%。",
        f"- 基线 Python/PyInstaller：{baseline['python_ui'].count} 个进程，{baseline['python_ui'].memory_mb:.1f} MB。",
        f"- 基线 Chrome/Chromium：{baseline['chromium'].count} 个进程，{baseline['chromium'].memory_mb:.1f} MB。",
        f"- 空闲 smoke Python/PyInstaller：{smoke['python_ui'].count} 个进程，{smoke['python_ui'].memory_mb:.1f} MB，平均 CPU {smoke['python_ui'].avg_cpu_percent:.2f}%，峰值 {smoke['python_ui'].peak_cpu_percent:.2f}%。",
        f"- 空闲 smoke Chrome/Chromium：{smoke['chromium'].count} 个进程，{smoke['chromium'].memory_mb:.1f} MB，平均 CPU {smoke['chromium'].avg_cpu_percent:.2f}%，峰值 {smoke['chromium'].peak_cpu_percent:.2f}%。",
        f"- 空闲 smoke 合计：{smoke['suite'].count} 个相关进程，{smoke['suite'].memory_mb:.1f} MB，平均 CPU {smoke['suite'].avg_cpu_percent:.2f}%，峰值 {smoke['suite'].peak_cpu_percent:.2f}%。",
        "",
        "## 环境噪音说明",
        "",
        "- 基线进程为采样前已经存在的 Python/PyInstaller 或 Chrome/Chromium，报告不把这些进程当成本次 exe 的独占增量。",
        "- smoke 进程优先按本次启动的 exe 及其子进程识别；无法归属的既有浏览器进程只作为环境噪音记录。",
        "- 空闲 smoke 不代表 4 账号登录、进房或真实下注后的资源占用。",
        "",
        "## 最终人工 exe 测试时采集",
        "",
    ]
    lines.extend([f"- {item}：{MANUAL_RESERVED_NOTE}" for item in manual_items])
    lines.extend(
        [
            "",
            "## 容量估算",
            "",
            f"- 内存可运行套数：{_format_int_or_unknown(capacity['memory_sets'])}",
            f"- CPU 可运行套数：{_format_int_or_unknown(capacity['cpu_sets'])}",
            f"- 浏览器进程可运行套数：{_format_int_or_unknown(capacity['browser_sets'])}（浏览器进程上限来源：本报告参数 browser_process_limit={data['browser_process_limit']}，属于假设上限）",
            f"- 最终建议套数：{_format_int_or_unknown(capacity['final_recommendation'])}",
            f"- 临时建议套数：{_format_int_or_unknown(capacity['temporary_recommendation'])}",
        ]
    )
    if capacity["final_recommendation"] is None:
        lines.append(
            f"- 结论：因缺少 {', '.join(capacity['missing_inputs'])}，最终建议降级为临时建议；4账号登录/进房/启动下注后的数据必须由最终人工 exe 测试补采。"
        )
    else:
        lines.append("- 结论：最终建议已按三项最小值乘以 0.7 安全系数向下取整。")
    lines.extend(
        [
            "",
            "## 合规结论",
            "",
            "- 本次只生成一次性文件报告，未新增常驻资源监控线程、后台定时器、主界面资源曲线或资源刷新配置。",
            "- 本次未修改下注核心、状态采集、坐标、预检或真实点击链路。",
        ]
    )
    return "\n".join(lines) + "\n"


def launch_exe(exe_path: Path) -> subprocess.Popen[Any] | None:
    if not exe_path.exists():
        return None
    return subprocess.Popen([str(exe_path)], cwd=str(exe_path.parent))


def close_process(process: subprocess.Popen[Any], timeout_seconds: float = 5.0) -> str:
    if process.poll() is not None:
        return "already-exited"
    if os.name == "nt":
        try:
            _post_wm_close(process.pid)
            process.wait(timeout=timeout_seconds)
            return "wm-close"
        except Exception:
            pass
    try:
        process.terminate()
        process.wait(timeout=timeout_seconds)
        return "terminate"
    except Exception:
        process.kill()
        return "kill"


def _post_wm_close(pid: int) -> None:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    enum_windows = user32.EnumWindows
    enum_windows_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    get_window_thread_process_id = user32.GetWindowThreadProcessId
    is_window_visible = user32.IsWindowVisible
    post_message = user32.PostMessageW
    WM_CLOSE = 0x0010

    def callback(hwnd: int, _: int) -> bool:
        window_pid = wintypes.DWORD()
        get_window_thread_process_id(hwnd, ctypes.byref(window_pid))
        if window_pid.value == pid and is_window_visible(hwnd):
            post_message(hwnd, WM_CLOSE, 0, 0)
        return True

    enum_windows(enum_windows_proc(callback), 0)


def collect_diagnostics(
    exe_path: Path,
    duration_seconds: float,
    interval_seconds: float,
    launch_smoke: bool,
    browser_process_limit: int,
) -> dict[str, Any]:
    system = read_system_snapshot()
    logical_cpus = max(1.0, system["processor_count"])
    baseline_processes = read_process_snapshot()
    baseline_pids = relevant_pids(baseline_processes)
    process: subprocess.Popen[Any] | None = None
    launched_pid: int | None = None
    mode = "current-process-sample"
    if launch_smoke:
        process = launch_exe(exe_path)
        if process is not None:
            launched_pid = process.pid
            mode = "idle-exe-smoke"
            time.sleep(2.0)
        else:
            mode = "current-process-sample-exe-missing"

    samples: list[tuple[float, dict[int, ProcessInfo]]] = []
    end_at = time.monotonic() + duration_seconds
    while time.monotonic() < end_at:
        samples.append((time.monotonic(), read_process_snapshot()))
        time.sleep(interval_seconds)
    samples.append((time.monotonic(), read_process_snapshot()))

    close_method = "not-launched"
    if process is not None:
        close_method = close_process(process)

    focus_pids_by_sample: list[set[int]] = []
    python_pids_by_sample: list[set[int]] = []
    chromium_pids_by_sample: list[set[int]] = []
    all_relevant_by_sample: list[set[int]] = []
    system_cpu_values: list[float] = []

    for index, (_, processes) in enumerate(samples):
        descendants = descendants_of(launched_pid, processes) if launched_pid is not None else set()
        new_relevant = relevant_pids(processes) - baseline_pids
        focus = set()
        if launched_pid is not None:
            focus.add(launched_pid)
        focus |= descendants
        focus |= new_relevant
        focus = {pid for pid in focus if pid in processes and pid in relevant_pids(processes)}
        focus_pids_by_sample.append(focus)
        python_pids_by_sample.append({pid for pid in focus if is_python_ui_process(processes[pid])})
        chromium_pids_by_sample.append({pid for pid in focus if is_chromium_process(processes[pid])})
        all_relevant_by_sample.append(relevant_pids(processes))
        if index > 0:
            previous_time, previous_processes = samples[index - 1]
            current_time, _ = samples[index]
            all_pids = set(previous_processes) | set(processes)
            system_cpu_values.append(
                cpu_percent_between(
                    previous_processes,
                    processes,
                    all_pids,
                    max(0.001, current_time - previous_time),
                    logical_cpus,
                )
            )

    baseline_python = {pid for pid in baseline_pids if is_python_ui_process(baseline_processes[pid])}
    baseline_chromium = {pid for pid in baseline_pids if is_chromium_process(baseline_processes[pid])}
    baseline_stats = {
        "python_ui": GroupStats(group_count(baseline_processes, baseline_python), group_memory(baseline_processes, baseline_python), 0.0, 0.0),
        "chromium": GroupStats(group_count(baseline_processes, baseline_chromium), group_memory(baseline_processes, baseline_chromium), 0.0, 0.0),
    }
    smoke_stats = {
        "python_ui": summarize_group(samples, python_pids_by_sample, logical_cpus),
        "chromium": summarize_group(samples, chromium_pids_by_sample, logical_cpus),
        "suite": summarize_group(samples, focus_pids_by_sample, logical_cpus),
    }
    capacity = estimate_capacity(
        free_memory_mb=system["free_memory_mb"],
        suite_memory_mb=smoke_stats["suite"].memory_mb,
        suite_cpu_percent=smoke_stats["suite"].avg_cpu_percent,
        suite_browser_processes=smoke_stats["chromium"].count,
        browser_process_limit=browser_process_limit,
    )
    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "duration_seconds": samples[-1][0] - samples[0][0] if len(samples) > 1 else 0.0,
        "mode": mode,
        "exe_path": str(exe_path),
        "exe_close_method": close_method,
        "browser_process_limit": browser_process_limit,
        "system": system,
        "system_cpu_avg_percent": sum(system_cpu_values) / len(system_cpu_values) if system_cpu_values else 0.0,
        "system_cpu_peak_percent": max(system_cpu_values) if system_cpu_values else 0.0,
        "baseline": baseline_stats,
        "smoke": smoke_stats,
        "capacity": capacity,
    }


def write_report(report: str, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = output_dir / f"stage11_resource_diagnostics_{stamp}.md"
    path.write_text(report, encoding="utf-8", newline="\n")
    return path


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a one-time resource diagnostics report.")
    parser.add_argument("--exe", type=Path, default=DEFAULT_EXE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--duration", type=float, default=16.0)
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--launch-smoke", action="store_true")
    parser.add_argument("--browser-process-limit", type=int, default=80)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if args.duration < 15:
        raise SystemExit("--duration must be at least 15 seconds")
    data = collect_diagnostics(
        exe_path=args.exe,
        duration_seconds=args.duration,
        interval_seconds=args.interval,
        launch_smoke=args.launch_smoke,
        browser_process_limit=args.browser_process_limit,
    )
    report = render_report(data)
    path = write_report(report, args.output_dir)
    print(f"report={path}")
    print(f"recommendation={_format_int_or_unknown(data['capacity']['final_recommendation'])}")
    print(f"temporary_recommendation={_format_int_or_unknown(data['capacity']['temporary_recommendation'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
