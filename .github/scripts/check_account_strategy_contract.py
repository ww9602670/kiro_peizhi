#!/usr/bin/env python3
"""Search gate for deprecated account/strategy contracts.

This script enforces two repository-level checks:
1) No new first-party runtime string usage of `/accounts/{id}/login`.
2) No static `game_type -> allowed_strategy_platform_types` export patterns.
"""

from __future__ import annotations

import ast
import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

LOGIN_SCAN_DIRS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("backend/app", ("*.py",)),
    ("frontend/src", ("*.ts", "*.tsx", "*.js", "*.jsx")),
)

# Match route-like string fragments such as:
# - /accounts/{account_id}/login
# - /api/v1/accounts/${id}/login
DEPRECATED_LOGIN_PATTERN = re.compile(r"/accounts/[^\s\"'`]+/login\b")

# Keep this explicit and minimal: only the deprecated alias route definition.
ALLOWED_DEPRECATED_LOGIN_LINE = re.compile(
    r'@router\.post\("/accounts/\{[^}]+\}/login",\s*deprecated=True\)'
)


@dataclass(frozen=True)
class Violation:
    category: str
    path: str
    line: int
    detail: str
    snippet: str


def _iter_runtime_files() -> list[Path]:
    files: list[Path] = []
    for root_rel, patterns in LOGIN_SCAN_DIRS:
        root = REPO_ROOT / root_rel
        if not root.exists():
            continue
        for pattern in patterns:
            files.extend(sorted(root.rglob(pattern)))
    return files


def _read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8", errors="replace").splitlines()


def _is_comment_line(text: str) -> bool:
    stripped = text.lstrip()
    return stripped.startswith("#") or stripped.startswith("//") or stripped.startswith("/*")


def _is_allowed_deprecated_alias(path: Path, line_text: str) -> bool:
    rel = path.relative_to(REPO_ROOT).as_posix()
    if rel != "backend/app/api/accounts.py":
        return False
    return bool(ALLOWED_DEPRECATED_LOGIN_LINE.search(line_text.strip()))


def check_deprecated_login_usage() -> list[Violation]:
    violations: list[Violation] = []
    for path in _iter_runtime_files():
        lines = _read_lines(path)
        rel = path.relative_to(REPO_ROOT).as_posix()
        for idx, line_text in enumerate(lines, start=1):
            if _is_comment_line(line_text):
                continue
            if not DEPRECATED_LOGIN_PATTERN.search(line_text):
                continue
            if _is_allowed_deprecated_alias(path, line_text):
                continue
            violations.append(
                Violation(
                    category="deprecated-login-contract",
                    path=rel,
                    line=idx,
                    detail="first-party runtime usage of deprecated /accounts/{id}/login contract",
                    snippet=line_text.strip(),
                )
            )
    return violations


def _is_allowed_strategy_target(node: ast.AST) -> bool:
    if isinstance(node, ast.Name):
        return node.id == "allowed_strategy_platform_types"
    if isinstance(node, ast.Subscript):
        key = node.slice
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            return key.value == "allowed_strategy_platform_types"
    return False


def _dict_key_is_allowed_strategy(node: ast.AST | None) -> bool:
    return isinstance(node, ast.Constant) and node.value == "allowed_strategy_platform_types"


def _contains_get_allowed_platform_types(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        if isinstance(func, ast.Name) and func.id == "get_allowed_platform_types":
            return True
        if isinstance(func, ast.Attribute) and func.attr == "get_allowed_platform_types":
            return True
    return False


def _contains_game_type_signal(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and child.id == "game_type":
            return True
        if isinstance(child, ast.Attribute) and child.attr == "game_type":
            return True
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            if child.value == "game_type":
                return True
    return False


def _build_static_export_violation(
    path: Path,
    line: int,
    reason: str,
) -> Violation:
    lines = _read_lines(path)
    snippet = lines[line - 1].strip() if 1 <= line <= len(lines) else ""
    return Violation(
        category="static-allowed-strategy-export",
        path=path.relative_to(REPO_ROOT).as_posix(),
        line=line,
        detail=reason,
        snippet=snippet,
    )


def check_static_allowed_strategy_export() -> list[Violation]:
    violations: list[Violation] = []
    for path in sorted((REPO_ROOT / "backend/app").rglob("*.py")):
        source = path.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:
            # Keep the gate robust if a file is mid-edit; syntax errors are handled by other CI jobs.
            continue

        for node in ast.walk(tree):
            value: ast.AST | None = None
            target_match = False

            if isinstance(node, ast.Assign):
                target_match = any(_is_allowed_strategy_target(t) for t in node.targets)
                value = node.value
            elif isinstance(node, ast.AnnAssign):
                target_match = _is_allowed_strategy_target(node.target)
                value = node.value
            elif isinstance(node, ast.Dict):
                for key, dict_value in zip(node.keys, node.values):
                    if not _dict_key_is_allowed_strategy(key):
                        continue
                    if _contains_get_allowed_platform_types(dict_value):
                        violations.append(
                            _build_static_export_violation(
                                path,
                                getattr(dict_value, "lineno", getattr(node, "lineno", 1)),
                                "allowed_strategy_platform_types derives from get_allowed_platform_types(...)",
                            )
                        )
                    elif _contains_game_type_signal(dict_value):
                        violations.append(
                            _build_static_export_violation(
                                path,
                                getattr(dict_value, "lineno", getattr(node, "lineno", 1)),
                                "allowed_strategy_platform_types derives from game_type",
                            )
                        )
                continue

            if not target_match or value is None:
                continue

            if _contains_get_allowed_platform_types(value):
                violations.append(
                    _build_static_export_violation(
                        path,
                        getattr(value, "lineno", getattr(node, "lineno", 1)),
                        "allowed_strategy_platform_types derives from get_allowed_platform_types(...)",
                    )
                )
            elif _contains_game_type_signal(value):
                violations.append(
                    _build_static_export_violation(
                        path,
                        getattr(value, "lineno", getattr(node, "lineno", 1)),
                        "allowed_strategy_platform_types derives from game_type",
                    )
                )

    return violations


def run_self_test() -> int:
    sample_login = '/api/v1/accounts/{account_id}/login'
    if not DEPRECATED_LOGIN_PATTERN.search(sample_login):
        print("self-test FAIL: login pattern did not match sample")
        return 1

    sample_alias = '@router.post("/accounts/{account_id}/login", deprecated=True)'
    if not ALLOWED_DEPRECATED_LOGIN_LINE.search(sample_alias):
        print("self-test FAIL: alias allowlist pattern did not match sample")
        return 1

    sample_expr = ast.parse(
        "allowed_strategy_platform_types = get_allowed_platform_types(account['game_type'])"
    ).body[0]
    if not isinstance(sample_expr, ast.Assign):
        print("self-test FAIL: could not parse assignment sample")
        return 1

    if not _contains_get_allowed_platform_types(sample_expr.value):
        print("self-test FAIL: get_allowed_platform_types detector mismatch")
        return 1

    if not _contains_game_type_signal(sample_expr.value):
        print("self-test FAIL: game_type detector mismatch")
        return 1

    print("account-strategy contract gate self-test: PASS")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check deprecated account/strategy contract usage."
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="run lightweight internal matcher self-checks",
    )
    args = parser.parse_args(argv)

    if args.self_test:
        return run_self_test()

    violations = check_deprecated_login_usage() + check_static_allowed_strategy_export()
    if not violations:
        print("account-strategy contract gate: PASS")
        return 0

    print("account-strategy contract gate: FAIL")
    for item in sorted(violations, key=lambda v: (v.category, v.path, v.line)):
        print(f"- [{item.category}] {item.path}:{item.line}: {item.detail}")
        if item.snippet:
            print(f"    {item.snippet}")
    print(
        "allowed deprecated alias: backend/app/api/accounts.py "
        '@router.post("/accounts/{account_id}/login", deprecated=True)'
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
