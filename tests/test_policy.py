from __future__ import annotations

from pathlib import Path

from cpacs_schema_tool.policy import load_policy


def test_bundled_policy_is_cpacs_standard() -> None:
    policy = load_policy()
    assert policy.policy_name == "CPACS Standard Schema Rules"
    assert policy.root_element == "cpacs"
    assert policy.root_type == "cpacsType"
    assert policy.rule("CPACS004", default_severity="warning").severity == "error"


def test_partial_override_merges_fields_and_lists(tmp_path: Path) -> None:
    override = tmp_path / "override.toml"
    override.write_text(
        """
[reachability]
keep_add = ["projectType"]

[naming.types]
exceptions_add = ["LegacyType"]

[rules.CPACS009]
severity = "error"
""",
        encoding="utf-8",
    )
    policy = load_policy([override])
    assert "projectType" in policy.reachability_keep
    assert "LegacyType" in policy.type_exceptions
    assert policy.rule("CPACS009", default_severity="warning").enabled is True
    assert policy.rule("CPACS009", default_severity="warning").severity == "error"
    assert len(policy.sources) == 2


def test_effective_policy_can_be_serialized() -> None:
    policy = load_policy()
    text = policy.to_toml()
    assert '[policy]' in text
    assert 'name = "CPACS Standard Schema Rules"' in text
