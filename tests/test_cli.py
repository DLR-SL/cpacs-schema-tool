from __future__ import annotations

from cpacs_schema_tool.cli import main


def test_cli_formats_and_checks(minimal_schema) -> None:
    assert main(["format", str(minimal_schema), "--in-place"]) == 0
    assert main(["check", str(minimal_schema)]) == 0


def test_cli_lint_returns_success_for_valid_minimal_schema(minimal_schema) -> None:
    assert main(["format", str(minimal_schema), "--in-place"]) == 0
    # Missing configured CPACS base types are warnings, not errors.
    assert main(["lint", str(minimal_schema), "--no-unused"]) == 0
