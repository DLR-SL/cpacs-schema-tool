"""Core XML Schema formatting, linting, dependency, and migration algorithms."""

from __future__ import annotations

import copy
import difflib
import os
import re
import shutil
import sys
import tempfile
from collections import deque
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

from lxml import etree

from .policy import SchemaPolicy

XSD_NS = "http://www.w3.org/2001/XMLSchema"
XLINK_NS = "http://www.w3.org/1999/xlink"
XSD = f"{{{XSD_NS}}}"
PARTICLE_TAGS = frozenset({"element", "group", "all", "choice", "sequence"})
GLOBAL_COMPONENT_KIND: Mapping[str, str] = {
    "complexType": "type",
    "simpleType": "type",
    "element": "element",
    "attribute": "attribute",
    "group": "group",
    "attributeGroup": "attributeGroup",
    "notation": "notation",
}
PRELUDE_TAGS = frozenset(
    {"annotation", "include", "import", "redefine", "override", "defaultOpenContent"}
)
REF_KIND_BY_OWNER = {
    "element": "element",
    "attribute": "attribute",
    "group": "group",
    "attributeGroup": "attributeGroup",
}
TYPE_REFERENCE_ATTRIBUTES = frozenset({"type", "base", "itemType"})


class SchemaToolError(RuntimeError):
    """Expected user-facing schema-tool failure."""


@dataclass(frozen=True, order=True)
class ComponentKey:
    kind: str
    name: str


@dataclass(frozen=True)
class Diagnostic:
    code: str
    severity: str
    message: str
    line: int | None = None
    xpath: str | None = None

    def __str__(self) -> str:
        location_parts: list[str] = []
        if self.line is not None:
            location_parts.append(f"line {self.line}")
        if self.xpath:
            location_parts.append(self.xpath)
        location = f" ({', '.join(location_parts)})" if location_parts else ""
        return f"{self.severity.upper()} {self.code}{location}: {self.message}"


def parser() -> etree.XMLParser:
    return etree.XMLParser(
        strip_cdata=False,
        remove_comments=False,
        remove_pis=False,
        resolve_entities=False,
        no_network=True,
        huge_tree=True,
    )


def parse_schema(path: Path) -> etree._ElementTree:
    if not path.is_file():
        raise SchemaToolError(f"Schema file not found: {path}")
    try:
        tree = etree.parse(str(path), parser())
    except (OSError, etree.XMLSyntaxError) as exc:
        raise SchemaToolError(f"Cannot parse XML schema {path}: {exc}") from exc
    root = tree.getroot()
    if root.tag != f"{XSD}schema":
        raise SchemaToolError(
            f"Root element must be {{{XSD_NS}}}schema, found {root.tag!r}."
        )
    return tree


def parse_text_as_schema(
    text: str, source_name: str = "<memory>"
) -> etree._ElementTree:
    try:
        root = etree.fromstring(
            text.encode("utf-8"), parser=parser(), base_url=source_name
        )
    except etree.XMLSyntaxError as exc:
        raise SchemaToolError(
            f"Generated schema is not well-formed XML: {exc}"
        ) from exc
    return etree.ElementTree(root)


def clone_tree(tree: etree._ElementTree) -> etree._ElementTree:
    return etree.ElementTree(copy.deepcopy(tree.getroot()))


def local_name(node: etree._Element) -> str | None:
    if not isinstance(node.tag, str):
        return None
    return etree.QName(node.tag).localname


def namespace_uri(node: etree._Element) -> str | None:
    if not isinstance(node.tag, str):
        return None
    return etree.QName(node.tag).namespace


def is_comment(node: etree._Element) -> bool:
    return isinstance(node, etree._Comment)


def normalized_comment_text(node: etree._Element) -> str:
    return " ".join((node.text or "").split())


def xpath_for(node: etree._Element) -> str | None:
    try:
        return node.getroottree().getpath(node)
    except (ValueError, AttributeError):
        return None


def diagnostic(
    code: str,
    severity: str,
    message: str,
    node: etree._Element | None = None,
) -> Diagnostic:
    return Diagnostic(
        code=code,
        severity=severity,
        message=message,
        line=node.sourceline if node is not None else None,
        xpath=xpath_for(node) if node is not None else None,
    )


def validate_schema(tree: etree._ElementTree) -> list[Diagnostic]:
    try:
        etree.XMLSchema(tree)
        return []
    except etree.XMLSchemaParseError as exc:
        result = [
            Diagnostic(
                code="XSD001",
                severity="error",
                message=entry.message,
                line=entry.line or None,
            )
            for entry in exc.error_log
        ]
        return result or [Diagnostic("XSD001", "error", str(exc))]


def expanded_attr_local_name(attribute_name: str) -> str:
    if attribute_name.startswith("{"):
        return etree.QName(attribute_name).localname
    return attribute_name


def arrange_attributes(root: etree._Element, policy: SchemaPolicy) -> None:
    rank = policy.attribute_rank
    for node in root.iter():
        if namespace_uri(node) != XSD_NS:
            continue
        lname = local_name(node)
        attributes = sorted(
            node.attrib.items(),
            key=lambda item: (
                rank.get(expanded_attr_local_name(item[0]), len(rank)),
                expanded_attr_local_name(item[0]).casefold(),
                item[0],
            ),
        )
        node.attrib.clear()
        for key, value in attributes:
            attr_local = expanded_attr_local_name(key)
            if (
                policy.remove_redundant_occurs_one
                and lname in PARTICLE_TAGS
                and attr_local in {"minOccurs", "maxOccurs"}
                and value == "1"
            ):
                continue
            node.set(key, value)


def declaration_sort_key(
    node: etree._Element,
    policy: SchemaPolicy,
) -> tuple[int, int, str, str]:
    lname = local_name(node) or ""
    name = node.get("name", "")
    base_rank = policy.base_type_rank
    if lname == "element" and name == policy.root_element:
        return (0, 0, "", "")
    if lname in {"complexType", "simpleType"} and name == policy.root_type:
        return (1, 0, "", "")
    if lname in {"complexType", "simpleType"} and name in base_rank:
        return (2, base_rank[name], "", "")
    return (3, 0, name.casefold(), lname)


def sort_top_level(root: etree._Element, policy: SchemaPolicy) -> None:
    children = list(root)
    for child in children:
        root.remove(child)

    prelude: list[etree._Element] = []
    blocks: list[tuple[list[etree._Element], etree._Element]] = []
    trailing: list[etree._Element] = []
    pending: list[etree._Element] = []
    declarations_started = False

    for child in children:
        if (
            is_comment(child)
            and normalized_comment_text(child) in policy.generated_section_comments
        ):
            continue
        lname = local_name(child)
        is_global = (
            namespace_uri(child) == XSD_NS
            and lname in GLOBAL_COMPONENT_KIND
            and child.get("name") is not None
        )
        if is_comment(child) or lname is None:
            pending.append(child)
            continue
        if (
            not declarations_started
            and namespace_uri(child) == XSD_NS
            and lname in PRELUDE_TAGS
        ):
            prelude.extend(pending)
            pending.clear()
            prelude.append(child)
            continue
        if is_global:
            declarations_started = True
            blocks.append((pending, child))
            pending = []
            continue
        trailing.extend(pending)
        pending.clear()
        trailing.append(child)

    trailing.extend(pending)
    blocks.sort(key=lambda block: declaration_sort_key(block[1], policy))
    root.extend(prelude)

    inserted_base = False
    inserted_custom = False
    for comments, declaration in blocks:
        key = declaration_sort_key(declaration, policy)
        if key[0] == 2 and policy.base_types_comment and not inserted_base:
            root.append(etree.Comment(f" {policy.base_types_comment} "))
            inserted_base = True
        if key[0] == 3 and policy.custom_types_comment and not inserted_custom:
            root.append(etree.Comment(f" {policy.custom_types_comment} "))
            inserted_custom = True
        root.extend(comments)
        root.append(declaration)

    root.extend(trailing)


def normalize_whitespace(root: etree._Element, policy: SchemaPolicy) -> None:
    etree.indent(root, space=" " * policy.indent_size)
    children = list(root)
    for index, child in enumerate(children):
        child.tail = (
            "\n" if index == len(children) - 1 else "\n\n" + " " * policy.indent_size
        )


def formatted_tree(
    tree: etree._ElementTree, policy: SchemaPolicy
) -> etree._ElementTree:
    result = clone_tree(tree)
    root = result.getroot()
    arrange_attributes(root, policy)
    sort_top_level(root, policy)
    normalize_whitespace(root, policy)
    return result


def read_input_preamble(input_path: Path) -> str:
    """Preserve comments or notices between the XML declaration and schema root."""
    raw = input_path.read_text(encoding="utf-8")
    declaration_end = raw.find("?>")
    search_start = declaration_end + 2 if declaration_end >= 0 else 0
    schema_match = re.search(r"<(?:[A-Za-z_][\w.-]*:)?schema\b", raw[search_start:])
    if schema_match is None:
        return ""
    schema_start = search_start + schema_match.start()
    preamble = raw[search_start:schema_start].strip()
    return preamble + "\n" if preamble else ""


def serialize_schema(
    tree: etree._ElementTree,
    input_path: Path,
    policy: SchemaPolicy,
) -> str:
    root_text = etree.tostring(
        tree.getroot(),
        encoding="unicode",
        pretty_print=False,
        with_tail=False,
    )
    root_text = root_text.replace("\t", " " * policy.indent_size)
    root_text = "\n".join(line.rstrip() for line in root_text.splitlines()) + "\n"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        + read_input_preamble(input_path)
        + root_text
    )


def validate_generated_text(text: str, source_name: str) -> None:
    errors = validate_schema(parse_text_as_schema(text, source_name))
    if errors:
        details = "\n".join(str(item) for item in errors[:20])
        raise SchemaToolError(
            "Generated output does not compile as XML Schema:\n" + details
        )


def resolve_output_target(
    input_path: Path,
    output: str | None,
    in_place: bool,
) -> Path | None:
    if in_place and output:
        raise SchemaToolError("Use either an output path or --in-place, not both.")
    if in_place:
        return input_path
    if output:
        target = Path(output)
        if target.resolve() == input_path.resolve():
            raise SchemaToolError(
                "Refusing to overwrite the input without the explicit --in-place option."
            )
        return target
    return None


def write_atomic(
    text: str,
    target: Path,
    *,
    source: Path,
    backup: bool,
    validate: bool,
) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if validate:
        validate_generated_text(text, str(target))
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temp_path = Path(handle.name)
        handle.write(text)
    try:
        if backup and target.exists():
            backup_path = target.with_suffix(target.suffix + ".bak")
            shutil.copy2(target, backup_path)
            print(f"Created backup: {backup_path}")
        try:
            shutil.copymode(target, temp_path)
        except FileNotFoundError:
            temp_path.chmod(0o644)
        os.replace(temp_path, target)
    finally:
        if temp_path.exists():
            temp_path.unlink()
    print(
        f"Updated schema in place: {target}"
        if target.resolve() == source.resolve()
        else f"Wrote schema: {target}"
    )


def output_schema_text(
    text: str,
    *,
    input_path: Path,
    output: str | None,
    in_place: bool,
    backup: bool,
    validate: bool,
) -> None:
    target = resolve_output_target(input_path, output, in_place)
    if target is None:
        if backup:
            raise SchemaToolError(
                "--backup requires --in-place or an output path; "
                "nothing is written when the result goes to stdout."
            )
        if validate:
            validate_generated_text(text, str(input_path))
        sys.stdout.write(text)
        return
    write_atomic(text, target, source=input_path, backup=backup, validate=validate)


def normalized_type_name(name: str, policy: SchemaPolicy) -> str:
    explicit = policy.explicit_type_renames.get(name)
    if explicit is not None:
        return explicit
    if name in policy.type_exceptions:
        return name
    result = name
    if policy.type_first_character == "lower" and result and result[0].isupper():
        result = result[0].lower() + result[1:]
    if policy.type_required_suffix and not result.endswith(policy.type_required_suffix):
        result += policy.type_required_suffix
    return result


def global_components(root: etree._Element) -> dict[ComponentKey, etree._Element]:
    components: dict[ComponentKey, etree._Element] = {}
    for child in root:
        if namespace_uri(child) != XSD_NS:
            continue
        kind = GLOBAL_COMPONENT_KIND.get(local_name(child) or "")
        name = child.get("name")
        if kind is None or name is None:
            continue
        key = ComponentKey(kind, name)
        if key in components:
            raise SchemaToolError(f"Duplicate global {kind} component named {name!r}.")
        components[key] = child
    return components


def split_lexical_qname(value: str) -> tuple[str | None, str]:
    if ":" in value:
        prefix, local = value.split(":", 1)
        return prefix, local
    return None, value


def resolve_lexical_qname(
    value: str,
    context: etree._Element,
) -> tuple[str | None, str, str | None]:
    prefix, local = split_lexical_qname(value)
    uri = context.nsmap.get(prefix) if prefix is not None else context.nsmap.get(None)
    return uri, local, prefix


def is_local_component_reference(uri: str | None, root: etree._Element) -> bool:
    target_namespace = root.get("targetNamespace")
    return uri == target_namespace if target_namespace else uri is None


def iter_qname_references(
    component: etree._Element,
    schema_root: etree._Element,
) -> Iterator[tuple[ComponentKey, etree._Element, str, str]]:
    for node in component.iter():
        for attribute_name in TYPE_REFERENCE_ATTRIBUTES:
            value = node.get(attribute_name)
            if not value:
                continue
            uri, name, _prefix = resolve_lexical_qname(value, node)
            if uri == XSD_NS or not is_local_component_reference(uri, schema_root):
                continue
            yield ComponentKey("type", name), node, attribute_name, value

        member_types = node.get("memberTypes")
        if member_types:
            for value in member_types.split():
                uri, name, _prefix = resolve_lexical_qname(value, node)
                if uri == XSD_NS or not is_local_component_reference(uri, schema_root):
                    continue
                yield ComponentKey("type", name), node, "memberTypes", value

        ref = node.get("ref")
        owner_kind = REF_KIND_BY_OWNER.get(local_name(node) or "")
        if ref and owner_kind:
            uri, name, _prefix = resolve_lexical_qname(ref, node)
            if uri != XSD_NS and is_local_component_reference(uri, schema_root):
                yield ComponentKey(owner_kind, name), node, "ref", ref

        substitution_group = node.get("substitutionGroup")
        if substitution_group:
            uri, name, _prefix = resolve_lexical_qname(substitution_group, node)
            if uri != XSD_NS and is_local_component_reference(uri, schema_root):
                yield ComponentKey(
                    "element", name
                ), node, "substitutionGroup", substitution_group


def reachable_components(
    root: etree._Element,
    components: Mapping[ComponentKey, etree._Element],
    start: Iterable[ComponentKey],
) -> tuple[set[ComponentKey], list[tuple[ComponentKey, etree._Element, str, str]]]:
    reachable: set[ComponentKey] = set()
    unresolved: list[tuple[ComponentKey, etree._Element, str, str]] = []
    queue: deque[ComponentKey] = deque(start)
    while queue:
        key = queue.popleft()
        if key in reachable:
            continue
        component = components.get(key)
        if component is None:
            unresolved.append((key, root, "root", key.name))
            continue
        reachable.add(key)
        for dependency, node, attribute, lexical_value in iter_qname_references(
            component, root
        ):
            if dependency not in components:
                unresolved.append((dependency, node, attribute, lexical_value))
            elif dependency not in reachable:
                queue.append(dependency)
    return reachable, unresolved


def unused_type_components(
    root: etree._Element,
    *,
    root_element: str,
    keep: Iterable[str] = (),
) -> tuple[list[ComponentKey], list[tuple[ComponentKey, etree._Element, str, str]]]:
    components = global_components(root)
    start = [ComponentKey("element", root_element)]
    for name in keep:
        matching = [key for key in components if key.name == name]
        if not matching:
            raise SchemaToolError(f"--keep component not found: {name}")
        start.extend(matching)
    reachable, unresolved = reachable_components(root, components, start)
    unused = sorted(
        key for key in components if key.kind == "type" and key not in reachable
    )
    return unused, unresolved


def lint_schema(
    tree: etree._ElementTree,
    policy: SchemaPolicy,
    *,
    include_unused: bool = True,
) -> list[Diagnostic]:
    root = tree.getroot()
    result = validate_schema(tree)

    def emit(
        code: str,
        message: str,
        *,
        node: etree._Element | None = None,
        default_severity: str = "warning",
    ) -> None:
        rule = policy.rule(code, default_severity=default_severity)
        if rule.enabled:
            result.append(diagnostic(code, rule.severity, message, node))

    try:
        components = global_components(root)
    except SchemaToolError as exc:
        emit("CPACS001", str(exc), default_severity="error")
        return result

    root_key = ComponentKey("element", policy.root_element)
    root_element = components.get(root_key)
    if root_element is None:
        emit(
            "CPACS002",
            f"Missing global root element {policy.root_element!r}.",
            default_severity="error",
        )
    else:
        root_type = root_element.get("type")
        _uri, root_type_local, _prefix = (
            resolve_lexical_qname(root_type, root_element)
            if root_type
            else (None, "", None)
        )
        if root_type_local != policy.root_type:
            emit(
                "CPACS003",
                f"Root element {policy.root_element!r} must use type "
                f"{policy.root_type!r}, found {root_type!r}.",
                node=root_element,
                default_severity="error",
            )

    type_components = {
        key: node for key, node in components.items() if key.kind == "type"
    }
    final_names: dict[str, list[str]] = {}
    for key, node in type_components.items():
        expected = normalized_type_name(key.name, policy)
        final_names.setdefault(expected, []).append(key.name)
        if expected != key.name:
            emit(
                "CPACS004",
                f"Global type {key.name!r} does not follow the configured CPACS "
                f"naming convention; expected {expected!r}.",
                node=node,
                default_severity="error",
            )

    for expected, originals in sorted(final_names.items()):
        if len(originals) > 1:
            emit(
                "CPACS005",
                f"Type-name normalization collision for {expected!r}: "
                + ", ".join(repr(name) for name in originals),
                default_severity="error",
            )

    for base_type in policy.base_types:
        if ComponentKey("type", base_type) not in components:
            emit(
                "CPACS006",
                f"Configured CPACS base type {base_type!r} is missing.",
                default_severity="warning",
            )

    if policy.remove_redundant_occurs_one:
        for node in root.iter():
            if namespace_uri(node) == XSD_NS and local_name(node) in PARTICLE_TAGS:
                for attr in ("minOccurs", "maxOccurs"):
                    if node.get(attr) == "1":
                        emit(
                            "CPACS007",
                            f'Explicit {attr}="1" is redundant.',
                            node=node,
                            default_severity="warning",
                        )

    seen_unresolved: set[tuple[ComponentKey, int | None, str, str]] = set()
    for component in components.values():
        for key, node, attribute, lexical_value in iter_qname_references(
            component, root
        ):
            if key in components:
                continue
            marker = (key, node.sourceline, attribute, lexical_value)
            if marker in seen_unresolved:
                continue
            seen_unresolved.add(marker)
            emit(
                "CPACS008",
                f"Unresolved local {key.kind} reference {lexical_value!r} in @{attribute}.",
                node=node,
                default_severity="error",
            )

    if include_unused and root_element is not None:
        try:
            unused, _ = unused_type_components(
                root,
                root_element=policy.root_element,
                keep=policy.reachability_keep,
            )
            for key in unused:
                emit(
                    "CPACS009",
                    f"Global type {key.name!r} is not reachable from "
                    f"the {policy.root_element!r} root element or configured roots.",
                    node=components[key],
                    default_severity="warning",
                )
        except SchemaToolError as exc:
            emit("CPACS009", str(exc), default_severity="error")

    xsd_prefix_uri = root.nsmap.get(policy.xsd_prefix)
    if xsd_prefix_uri != XSD_NS:
        emit(
            "CPACS010",
            f"The configured XSD prefix {policy.xsd_prefix!r} must resolve to "
            f"{XSD_NS!r}, found {xsd_prefix_uri!r}.",
            default_severity="error",
        )

    xlink_prefix_uri = root.nsmap.get(policy.xlink_prefix)
    if xlink_prefix_uri is not None and xlink_prefix_uri != XLINK_NS:
        emit(
            "CPACS011",
            f"The configured XLink prefix {policy.xlink_prefix!r} must resolve "
            f"to {XLINK_NS!r}, found {xlink_prefix_uri!r}.",
            default_severity="error",
        )
    return result


def rewrite_type_qname(
    lexical_value: str,
    context: etree._Element,
    schema_root: etree._Element,
    rename_map: Mapping[str, str],
) -> str:
    uri, local, prefix = resolve_lexical_qname(lexical_value, context)
    if uri == XSD_NS or not is_local_component_reference(uri, schema_root):
        return lexical_value
    replacement = rename_map.get(local)
    if replacement is None:
        return lexical_value
    return f"{prefix}:{replacement}" if prefix else replacement


def apply_type_renames(root: etree._Element, rename_map: Mapping[str, str]) -> None:
    for child in root:
        if namespace_uri(child) == XSD_NS and local_name(child) in {
            "complexType",
            "simpleType",
        }:
            name = child.get("name")
            if name in rename_map:
                child.set("name", rename_map[name])
    for node in root.iter():
        for attribute_name in TYPE_REFERENCE_ATTRIBUTES:
            value = node.get(attribute_name)
            if value:
                node.set(
                    attribute_name,
                    rewrite_type_qname(value, node, root, rename_map),
                )
        member_types = node.get("memberTypes")
        if member_types:
            node.set(
                "memberTypes",
                " ".join(
                    rewrite_type_qname(value, node, root, rename_map)
                    for value in member_types.split()
                ),
            )


def rename_plan(root: etree._Element, policy: SchemaPolicy) -> dict[str, str]:
    components = global_components(root)
    type_names = sorted(key.name for key in components if key.kind == "type")
    unknown = sorted(set(policy.explicit_type_renames).difference(type_names))
    if unknown:
        raise SchemaToolError(
            "Configured [renames.types] source names are not global types: "
            + ", ".join(repr(name) for name in unknown)
        )
    rename_map = {
        name: normalized_type_name(name, policy)
        for name in type_names
        if normalized_type_name(name, policy) != name
    }
    final_to_originals: dict[str, list[str]] = {}
    for name in type_names:
        final_to_originals.setdefault(rename_map.get(name, name), []).append(name)
    collisions = {
        final: originals
        for final, originals in final_to_originals.items()
        if len(originals) > 1
    }
    if collisions:
        details = "; ".join(
            f"{final!r} <- {', '.join(repr(name) for name in originals)}"
            for final, originals in sorted(collisions.items())
        )
        raise SchemaToolError(f"Cannot rename types due to collisions: {details}")
    return rename_map


def format_schema_file(
    input_path: Path,
    policy: SchemaPolicy,
    *,
    output: str | None,
    in_place: bool,
    backup: bool,
    validate: bool,
) -> None:
    tree = parse_schema(input_path)
    text = serialize_schema(formatted_tree(tree, policy), input_path, policy)
    output_schema_text(
        text,
        input_path=input_path,
        output=output,
        in_place=in_place,
        backup=backup,
        validate=validate,
    )


def check_schema_file(
    input_path: Path,
    policy: SchemaPolicy,
    *,
    validate: bool,
    diff_lines: int,
) -> int:
    tree = parse_schema(input_path)
    formatted = serialize_schema(formatted_tree(tree, policy), input_path, policy)
    original = input_path.read_text(encoding="utf-8")
    if validate:
        errors = validate_schema(tree)
        if errors:
            for item in errors:
                print(item, file=sys.stderr)
            return 2
        validate_generated_text(formatted, str(input_path))
    if original == formatted:
        print("Schema formatting OK. The schema is in canonical form.")
        return 0
    print(
        "Schema formatting differs from the canonical representation.", file=sys.stderr
    )
    print("Run the format command and review the changes.", file=sys.stderr)
    diff = difflib.unified_diff(
        original.splitlines(),
        formatted.splitlines(),
        fromfile=str(input_path),
        tofile=f"{input_path} (formatted)",
        lineterm="",
        n=3,
    )
    for line_number, line in enumerate(diff):
        if line_number >= diff_lines:
            print("... diff truncated ...", file=sys.stderr)
            break
        print(line, file=sys.stderr)
    return 1
