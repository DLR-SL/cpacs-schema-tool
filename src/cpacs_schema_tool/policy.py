"""Loading, layering, validation, and serialization of CPACS schema policies."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Sequence


try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib


ALLOWED_SEVERITIES = frozenset({"error", "warning", "info"})
DEFAULT_POLICY_RESOURCE = files("cpacs_schema_tool.resources").joinpath("schema_rules.toml")


class PolicyError(RuntimeError):
    """Raised when a schema policy cannot be loaded or validated."""


@dataclass(frozen=True)
class LintRulePolicy:
    enabled: bool
    severity: str


@dataclass(frozen=True)
class SchemaPolicy:
    """Validated effective policy used by the schema algorithms."""

    raw: Mapping[str, Any]
    sources: tuple[str, ...]
    policy_name: str
    policy_version: str
    root_element: str
    root_type: str
    indent_size: int
    remove_redundant_occurs_one: bool
    attribute_order: tuple[str, ...]
    base_types_comment: str
    custom_types_comment: str
    base_types: tuple[str, ...]
    type_first_character: str
    type_required_suffix: str
    type_exceptions: frozenset[str]
    reachability_keep: tuple[str, ...]
    xsd_prefix: str
    xlink_prefix: str
    rules: Mapping[str, LintRulePolicy]
    explicit_type_renames: Mapping[str, str]

    @property
    def attribute_rank(self) -> Mapping[str, int]:
        return {name: index for index, name in enumerate(self.attribute_order)}

    @property
    def base_type_rank(self) -> Mapping[str, int]:
        return {name: index for index, name in enumerate(self.base_types)}

    @property
    def generated_section_comments(self) -> frozenset[str]:
        return frozenset(
            value
            for value in (self.base_types_comment, self.custom_types_comment)
            if value
        )

    def rule(self, code: str, *, default_severity: str) -> LintRulePolicy:
        return self.rules.get(
            code,
            LintRulePolicy(enabled=True, severity=default_severity),
        )

    def to_toml(self) -> str:
        """Serialize the effective configuration for diagnostics or pinning."""
        return _toml_dumps(self.raw)


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    raise PolicyError(f"Cannot serialize policy value as TOML: {value!r}")


def _toml_dumps(raw: Mapping[str, Any]) -> str:
    lines: list[str] = []

    def emit_table(table: Mapping[str, Any], path: tuple[str, ...]) -> None:
        scalar_items = [(key, value) for key, value in table.items() if not isinstance(value, Mapping)]
        child_items = [(key, value) for key, value in table.items() if isinstance(value, Mapping)]
        if path:
            if lines and lines[-1] != "":
                lines.append("")
            lines.append("[" + ".".join(path) + "]")
        for key, value in scalar_items:
            lines.append(f"{key} = {_toml_value(value)}")
        for key, value in child_items:
            emit_table(value, (*path, str(key)))

    root_scalars = {key: value for key, value in raw.items() if not isinstance(value, Mapping)}
    root_tables = {key: value for key, value in raw.items() if isinstance(value, Mapping)}
    for key, value in root_scalars.items():
        lines.append(f"{key} = {_toml_value(value)}")
    for key, value in root_tables.items():
        emit_table(value, (str(key),))
    return "\n".join(lines).rstrip() + "\n"


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise PolicyError(f"Schema rules file not found: {path}")
    try:
        with path.open("rb") as handle:
            value = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise PolicyError(f"Cannot read schema rules {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PolicyError(f"Schema rules root must be a TOML table: {path}")
    return value


def _read_default_policy() -> dict[str, Any]:
    try:
        with DEFAULT_POLICY_RESOURCE.open("rb") as handle:
            value = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise PolicyError(f"Cannot read bundled CPACS policy: {exc}") from exc
    if not isinstance(value, dict):
        raise PolicyError("Bundled CPACS policy root must be a TOML table.")
    return value


def _deep_merge(base: MutableMapping[str, Any], override: Mapping[str, Any]) -> None:
    """Merge a partial override into a policy.

    Mapping values are merged recursively. Ordinary lists replace their defaults.
    Keys ending in ``_add`` append unique values to the corresponding list, while
    ``_remove`` removes values. This makes small project policies possible without
    copying the complete CPACS standard policy.
    """

    regular_items: list[tuple[str, Any]] = []
    list_operations: list[tuple[str, str, Any]] = []

    for key, value in override.items():
        if key.endswith("_add"):
            list_operations.append((key[:-4], "add", value))
        elif key.endswith("_remove"):
            list_operations.append((key[:-7], "remove", value))
        else:
            regular_items.append((key, value))

    for key, value in regular_items:
        current = base.get(key)
        if isinstance(current, MutableMapping) and isinstance(value, Mapping):
            _deep_merge(current, value)
        else:
            base[key] = copy.deepcopy(value)

    for target, operation, values in list_operations:
        if not isinstance(values, list):
            raise PolicyError(f"Override key {target}_{operation} must be an array.")
        current = base.get(target)
        if not isinstance(current, list):
            raise PolicyError(
                f"Override key {target}_{operation} requires an existing list {target!r}."
            )
        if operation == "add":
            for value in values:
                if value not in current:
                    current.append(copy.deepcopy(value))
        else:
            base[target] = [value for value in current if value not in values]


def _table(raw: Mapping[str, Any], key: str, *, path: str) -> Mapping[str, Any]:
    value = raw.get(key)
    if not isinstance(value, Mapping):
        raise PolicyError(f"Missing or invalid TOML table [{path}].")
    return value


def _required_string(raw: Mapping[str, Any], key: str, *, path: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise PolicyError(f"{path}.{key} must be a non-empty string.")
    return value


def _string_list(value: Any, *, path: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise PolicyError(f"{path} must be an array of strings.")
    seen: set[str] = set()
    duplicates: list[str] = []
    for item in value:
        if item in seen and item not in duplicates:
            duplicates.append(item)
        seen.add(item)
    if duplicates:
        raise PolicyError(
            f"{path} contains duplicate values: " + ", ".join(repr(x) for x in duplicates)
        )
    return tuple(value)


def _validate(raw: Mapping[str, Any], sources: Sequence[str]) -> SchemaPolicy:
    policy_meta = _table(raw, "policy", path="policy")
    schema = _table(raw, "schema", path="schema")
    formatting = _table(raw, "format", path="format")
    naming = _table(raw, "naming", path="naming")
    naming_types = _table(naming, "types", path="naming.types")
    reachability = _table(raw, "reachability", path="reachability")
    prefixes = _table(raw, "prefixes", path="prefixes")
    rules_raw = _table(raw, "rules", path="rules")
    renames = _table(raw, "renames", path="renames")
    rename_types = _table(renames, "types", path="renames.types")

    indent_size = formatting.get("indent_size")
    if not isinstance(indent_size, int) or isinstance(indent_size, bool) or indent_size < 1:
        raise PolicyError("format.indent_size must be a positive integer.")

    remove_occurs = formatting.get("remove_redundant_occurs_one")
    if not isinstance(remove_occurs, bool):
        raise PolicyError("format.remove_redundant_occurs_one must be true or false.")

    first_character = _required_string(
        naming_types,
        "first_character",
        path="naming.types",
    )
    if first_character not in {"lower", "unchanged"}:
        raise PolicyError("naming.types.first_character must be 'lower' or 'unchanged'.")

    rule_policies: dict[str, LintRulePolicy] = {}
    for code, value in rules_raw.items():
        if not isinstance(value, Mapping):
            raise PolicyError(f"rules.{code} must be a TOML table.")
        enabled = value.get("enabled", True)
        severity = value.get("severity", "warning")
        if not isinstance(enabled, bool):
            raise PolicyError(f"rules.{code}.enabled must be true or false.")
        if not isinstance(severity, str) or severity not in ALLOWED_SEVERITIES:
            raise PolicyError(
                f"rules.{code}.severity must be one of: "
                + ", ".join(sorted(ALLOWED_SEVERITIES))
            )
        rule_policies[str(code)] = LintRulePolicy(enabled=enabled, severity=severity)

    explicit_renames: dict[str, str] = {}
    for old, new in rename_types.items():
        if not isinstance(old, str) or not isinstance(new, str) or not new.strip():
            raise PolicyError(
                "Every entry in [renames.types] must map a name to a non-empty string."
            )
        explicit_renames[old] = new

    policy = SchemaPolicy(
        raw=copy.deepcopy(dict(raw)),
        sources=tuple(sources),
        policy_name=_required_string(policy_meta, "name", path="policy"),
        policy_version=_required_string(policy_meta, "version", path="policy"),
        root_element=_required_string(schema, "root_element", path="schema"),
        root_type=_required_string(schema, "root_type", path="schema"),
        indent_size=indent_size,
        remove_redundant_occurs_one=remove_occurs,
        attribute_order=_string_list(
            formatting.get("attribute_order"),
            path="format.attribute_order",
        ),
        base_types_comment=str(formatting.get("base_types_comment", "")),
        custom_types_comment=str(formatting.get("custom_types_comment", "")),
        base_types=_string_list(formatting.get("base_types"), path="format.base_types"),
        type_first_character=first_character,
        type_required_suffix=_required_string(
            naming_types,
            "required_suffix",
            path="naming.types",
        ),
        type_exceptions=frozenset(
            _string_list(naming_types.get("exceptions", []), path="naming.types.exceptions")
        ),
        reachability_keep=_string_list(
            reachability.get("keep", []),
            path="reachability.keep",
        ),
        xsd_prefix=_required_string(prefixes, "xsd", path="prefixes"),
        xlink_prefix=_required_string(prefixes, "xlink", path="prefixes"),
        rules=rule_policies,
        explicit_type_renames=explicit_renames,
    )

    if policy.root_type in policy.base_types:
        raise PolicyError("schema.root_type must not also be listed in format.base_types.")
    return policy


def load_policy(
    override_paths: Sequence[Path] = (),
    *,
    replace_path: Path | None = None,
) -> SchemaPolicy:
    """Load the bundled CPACS defaults and apply optional project overrides.

    ``override_paths`` are applied in order. ``replace_path`` bypasses the bundled
    defaults and therefore must contain a complete policy.
    """

    if replace_path is not None and override_paths:
        raise PolicyError("Use either partial --rules overrides or --replace-rules, not both.")

    if replace_path is not None:
        raw = _read_toml(replace_path)
        sources = [str(replace_path.resolve())]
    else:
        raw = _read_default_policy()
        sources = ["package:cpacs_schema_tool/resources/schema_rules.toml"]
        for path in override_paths:
            override = _read_toml(path)
            _deep_merge(raw, override)
            sources.append(str(path.resolve()))

    return _validate(raw, sources)
