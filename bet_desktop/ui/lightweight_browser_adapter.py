"""Browser control adapter abstraction for lightweight dashboard."""

from __future__ import annotations

import shlex
import subprocess
from collections.abc import Callable


class BrowserControlAdapter:
    """Adapter protocol used by controller and dashboard."""

    def __init__(self, max_log_entries: int = 200, on_log: Callable[[str], None] | None = None) -> None:
        self.max_log_entries = max_log_entries
        self.on_log = on_log
        self._log_lines: list[str] = []

    @property
    def log_lines(self) -> list[str]:
        return list(self._log_lines)

    def _append_log(self, message: str) -> None:
        self._log_lines.append(message)
        if len(self._log_lines) > self.max_log_entries:
            self._log_lines = self._log_lines[-self.max_log_entries :]
        if self.on_log:
            self.on_log(message)

    def _record_action(self, action: str, account_ids: list[str], details: str = "") -> str:
        normalized = ",".join(account_ids) if account_ids else "-"
        line = f"[{action}] accounts={normalized}"
        if details:
            line += f" details={details}"
        self._append_log(line)
        return line

    def run_command(self, command: list[str] | tuple[str, ...]) -> tuple[int, str, str]:
        raise NotImplementedError

    def start_accounts(self, account_ids: list[str]) -> tuple[int, str, str]:
        return self.run_command(["start_accounts", *account_ids])

    def fill_login(self, account_ids: list[str]) -> tuple[int, str, str]:
        return self.run_command(["fill_login", *account_ids])

    def handoff_to_headless(self, account_ids: list[str]) -> tuple[int, str, str]:
        return self.run_command(["handoff_to_headless", *account_ids])

    def enter_room(self, account_ids: list[str], room_index: int) -> tuple[int, str, str]:
        return self.run_command(["enter_room", str(room_index), *account_ids])

    def release_headless(self, account_ids: list[str]) -> tuple[int, str, str]:
        return self.run_command(["release_headless", *account_ids])

    def stop_accounts(self, account_ids: list[str]) -> tuple[int, str, str]:
        return self.run_command(["stop_accounts", *account_ids])


class FakeBrowserControlAdapter(BrowserControlAdapter):
    """Safe adapter used for phase-1/3; only emits logs and records commands."""

    def __init__(
        self,
        max_log_entries: int = 200,
        on_log: Callable[[str], None] | None = None,
        max_command_log: int = 500,
    ) -> None:
        super().__init__(max_log_entries=max_log_entries, on_log=on_log)
        self._max_command_log = max_command_log
        self._commands: list[str] = []

    @property
    def commands(self) -> list[str]:
        return list(self._commands)

    def _append_command(self, command: list[str]) -> None:
        rendered = shlex.join(command)
        self._commands.append(rendered)
        if len(self._commands) > self._max_command_log:
            self._commands = self._commands[-self._max_command_log :]

    def run_command(self, command: list[str] | tuple[str, ...]) -> tuple[int, str, str]:
        command = list(command)
        self._append_command(command)
        action = command[0] if command else "noop"
        accounts = command[1:] if len(command) > 1 else []
        line = self._record_action(f"fake:{action}", accounts)
        return 0, line, ""

    def start_accounts(self, account_ids: list[str]) -> tuple[int, str, str]:
        self._record_action("start", account_ids)
        return super().start_accounts(account_ids)

    def fill_login(self, account_ids: list[str]) -> tuple[int, str, str]:
        self._record_action("fill_login", account_ids)
        return super().fill_login(account_ids)

    def handoff_to_headless(self, account_ids: list[str]) -> tuple[int, str, str]:
        self._record_action("handoff_to_headless", account_ids)
        return super().handoff_to_headless(account_ids)

    def enter_room(self, account_ids: list[str], room_index: int) -> tuple[int, str, str]:
        self._record_action("enter_room", account_ids, f"room_index={room_index}")
        return super().enter_room(account_ids, room_index)

    def release_headless(self, account_ids: list[str]) -> tuple[int, str, str]:
        self._record_action("release_headless", account_ids)
        return super().release_headless(account_ids)

    def stop_accounts(self, account_ids: list[str]) -> tuple[int, str, str]:
        self._record_action("stop", account_ids)
        return super().stop_accounts(account_ids)


class CommandBrowserControlAdapter(BrowserControlAdapter):
    """Command wrapper adapter for local execution (not enabled by default)."""

    def __init__(self, command: str = "python", max_log_entries: int = 200, on_log: Callable[[str], None] | None = None) -> None:
        super().__init__(max_log_entries=max_log_entries, on_log=on_log)
        self.command = command

    def run_command(self, command: list[str] | tuple[str, ...]) -> tuple[int, str, str]:
        command = [self.command, *command]
        self._append_log(f"run command: {shlex.join(command)}")
        try:
            completed = subprocess.run(command, capture_output=True, text=True, check=False)
        except OSError as exc:  # pragma: no cover - depends on external command
            message = str(exc)
            self._append_log(f"command failed: {message}")
            return 1, "", message
        return completed.returncode, completed.stdout, completed.stderr
