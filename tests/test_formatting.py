from __future__ import annotations

from cpacs_schema_tool.core import check_schema_file, format_schema_file
from cpacs_schema_tool.policy import load_policy


def test_format_is_idempotent(minimal_schema) -> None:
    policy = load_policy()
    format_schema_file(
        minimal_schema,
        policy,
        output=None,
        in_place=True,
        backup=False,
        validate=True,
    )
    first = minimal_schema.read_text(encoding="utf-8")
    format_schema_file(
        minimal_schema,
        policy,
        output=None,
        in_place=True,
        backup=False,
        validate=True,
    )
    second = minimal_schema.read_text(encoding="utf-8")
    assert first == second
    assert 'minOccurs="1"' not in second
    assert 'maxOccurs="1"' not in second
    assert check_schema_file(minimal_schema, policy, validate=True, diff_lines=20) == 0
