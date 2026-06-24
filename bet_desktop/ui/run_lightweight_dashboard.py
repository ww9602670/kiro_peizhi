"""Launcher for the lightweight hedge dashboard."""

from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtWidgets import QApplication


def main() -> int:
    project_root = Path(__file__).resolve().parents[2]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from bet_desktop.ui.lightweight_cluster_adapter import LightweightClusterAdapter
    from bet_desktop.ui.lightweight_controller import LightweightController
    from bet_desktop.ui.lightweight_dashboard import LightweightDashboard

    app = QApplication(sys.argv)
    window = LightweightDashboard(controller=LightweightController(adapter=LightweightClusterAdapter()))
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
