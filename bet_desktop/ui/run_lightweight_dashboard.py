"""Launcher for the lightweight hedge dashboard."""

from __future__ import annotations

import faulthandler
import sys
import traceback
from pathlib import Path

from PyQt6.QtWidgets import QApplication

_CRASH_LOG_FILE = None


def _install_crash_logging(project_root: Path) -> None:
    global _CRASH_LOG_FILE
    log_dir = project_root / "dist" / "BetDesktop" / "live_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    _CRASH_LOG_FILE = (log_dir / "lightweight_ui_crash.log").open("a", encoding="utf-8", buffering=1)
    faulthandler.enable(_CRASH_LOG_FILE, all_threads=True)

    def _excepthook(exc_type, exc, tb) -> None:
        traceback.print_exception(exc_type, exc, tb, file=_CRASH_LOG_FILE)

    sys.excepthook = _excepthook


def main() -> int:
    if getattr(sys, "frozen", False):
        project_root = Path(sys.executable).resolve().parent
    else:
        project_root = Path(__file__).resolve().parents[2]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    _install_crash_logging(project_root)
    from bet_desktop.ui.lightweight_controller import LightweightController
    from bet_desktop.ui.lightweight_dashboard import LightweightDashboard
    from bet_desktop.ui.lightweight_probe_adapter import LightweightProbeAdapter

    app = QApplication(sys.argv)
    controller = LightweightController(adapter=LightweightProbeAdapter())
    app.aboutToQuit.connect(controller.shutdown_runtime)
    window = LightweightDashboard(controller=controller)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
