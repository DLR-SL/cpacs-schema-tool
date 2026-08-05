"""Command-line interface for the CPACS Schema Tool."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from . import __version__
from .core import (
    SchemaToolError,
    apply_type_renames,
    check_schema_file,
    diagnostic,
    format_schema_file,
    formatted_tree,
    global_components,
    lint_schema,
    output_schema_text,
    parse_schema,
    rename_plan,
    serialize_schema,
    unused_type_components,
)
from .policy import PolicyError, SchemaPolicy, load_policy


def add_policy_arguments(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--rules",
        type=Path,
        action="append",
        default=[],
        metavar="PATH",
        help=(
            "Apply a partial policy override over the bundled CPACS defaults. "
            "May be repeated; later files take precedence."
        ),
    )
    group.add_argument(
        "--replace-rules",
        type=Path,
        metavar="PATH",
        help="Use a complete policy instead of the bundled CPACS defaults.",
    )
    parser.add_argument(
        "--print-effective-rules",
        action="store_true",
        help="Print the merged effective policy as TOML and exit.",
    )


def add_write_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "output",
        nargs="?",
        help="Output schema path. Without output or --in-place, write to stdout.",
    )
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="Replace the input schema atomically.",
    )
    parser.add_argument(
        "--backup",
        action="store_true",
        help="Create <schema>.bak before an in-place replacement.",
    )
    parser.add_argument(
        "--no-validate",
        action="store_true",
        help="Skip XSD compilation of generated output.",
    )


def resolve_policy(args: argparse.Namespace) -> SchemaPolicy:
    policy = load_policy(args.rules, replace_path=args.replace_rules)
    if args.print_effective_rules:
        sys.stdout.write(policy.to_toml())
        raise SystemExit(0)
    return policy


def command_format(args: argparse.Namespace, policy: SchemaPolicy) -> int:
    format_schema_file(
        Path(args.schema),
        policy,
        output=args.output,
        in_place=args.in_place,
        backup=args.backup,
        validate=not args.no_validate,
    )
    return 0


def command_check(args: argparse.Namespace, policy: SchemaPolicy) -> int:
    return check_schema_file(
        Path(args.schema),
        policy,
        validate=not args.no_validate,
        diff_lines=args.diff_lines,
    )


def command_lint(args: argparse.Namespace, policy: SchemaPolicy) -> int:
    diagnostics = lint_schema(
        parse_schema(Path(args.schema)),
        policy,
        include_unused=not args.no_unused,
    )
    diagnostics.sort(
        key=lambda item: (
            0 if item.severity == "error" else 1,
            item.code,
            item.line or 0,
            item.message,
        )
    )
    for item in diagnostics:
        stream = sys.stderr if item.severity == "error" else sys.stdout
        print(item, file=stream)
    errors = sum(item.severity == "error" for item in diagnostics)
    warnings = sum(item.severity == "warning" for item in diagnostics)
    print(f"Lint result: {errors} error(s), {warnings} warning(s).")
    if errors or (warnings and args.warnings_as_errors):
        return 1
    return 0


def command_rename_types(args: argparse.Namespace, policy: SchemaPolicy) -> int:
    input_path = Path(args.schema)
    tree = parse_schema(input_path)
    rename_map = rename_plan(tree.getroot(), policy)
    if not rename_map:
        print("All global type names already follow the CPACS convention.")
        return 0
    print("Proposed type renames:")
    for old, new in sorted(rename_map.items()):
        print(f"  {old} -> {new}")
    if not args.apply:
        print("Dry run only. Re-run with --apply to write the migration.")
        return 0
    apply_type_renames(tree.getroot(), rename_map)
    text = serialize_schema(formatted_tree(tree, policy), input_path, policy)
    output_schema_text(
        text,
        input_path=input_path,
        output=args.output,
        in_place=args.in_place,
        backup=args.backup,
        validate=not args.no_validate,
    )
    return 0


def command_prune_unused(args: argparse.Namespace, policy: SchemaPolicy) -> int:
    input_path = Path(args.schema)
    tree = parse_schema(input_path)
    root = tree.getroot()
    root_element = args.root_element or policy.root_element
    keep = tuple(policy.reachability_keep) + tuple(args.keep)
    unused, unresolved = unused_type_components(
        root,
        root_element=root_element,
        keep=keep,
    )
    if unresolved:
        print("Unresolved references prevent safe pruning:", file=sys.stderr)
        for key, node, attribute, lexical_value in unresolved[:50]:
            print(
                diagnostic(
                    "CPACS008",
                    "error",
                    f"Unresolved local {key.kind} reference {lexical_value!r} "
                    f"in @{attribute}.",
                    node,
                ),
                file=sys.stderr,
            )
        return 2
    if not unused:
        print("No unreachable global types found.")
        return 0
    print("Unreachable global types:")
    for key in unused:
        print(f"  {key.name}")
    if not args.apply:
        print("Dry run only. Re-run with --apply to remove these types.")
        return 0
    components = global_components(root)
    for key in unused:
        root.remove(components[key])
    text = serialize_schema(formatted_tree(tree, policy), input_path, policy)
    output_schema_text(
        text,
        input_path=input_path,
        output=args.output,
        in_place=args.in_place,
        backup=args.backup,
        validate=not args.no_validate,
    )
    return 0


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cpacs-schema",
        description="Format, check, lint, and explicitly migrate CPACS XSD schemas.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    format_parser = subparsers.add_parser(
        "format",
        help="Apply safe, deterministic formatting without renaming or pruning.",
    )
    format_parser.add_argument("schema", help="Input XSD file")
    add_policy_arguments(format_parser)
    add_write_arguments(format_parser)
    format_parser.set_defaults(handler=command_format)

    check_parser = subparsers.add_parser(
        "check",
        help="Check whether a schema matches the canonical formatted form.",
    )
    check_parser.add_argument("schema", help="Input XSD file")
    add_policy_arguments(check_parser)
    check_parser.add_argument(
        "--diff-lines",
        type=int,
        default=200,
        help="Maximum number of unified-diff lines to print (default: 200).",
    )
    check_parser.add_argument("--no-validate", action="store_true", help="Skip XSD compilation.")
    check_parser.set_defaults(handler=command_check)

    lint_parser = subparsers.add_parser(
        "lint",
        help="Check CPACS conventions, references, reachability, and XSD validity.",
    )
    lint_parser.add_argument("schema", help="Input XSD file")
    add_policy_arguments(lint_parser)
    lint_parser.add_argument(
        "--no-unused",
        action="store_true",
        help="Do not report types unreachable from the configured root element.",
    )
    lint_parser.add_argument(
        "--warnings-as-errors",
        action="store_true",
        help="Return a failing exit code when warnings are present.",
    )
    lint_parser.set_defaults(handler=command_lint)

    rename_parser = subparsers.add_parser(
        "rename-types",
        help="Explicitly normalize global type names and update type references.",
    )
    rename_parser.add_argument("schema", help="Input XSD file")
    add_policy_arguments(rename_parser)
    rename_parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the migration. Without this flag, only print the plan.",
    )
    add_write_arguments(rename_parser)
    rename_parser.set_defaults(handler=command_rename_types)

    prune_parser = subparsers.add_parser(
        "prune-unused",
        help="Explicitly remove global types unreachable from a root element.",
    )
    prune_parser.add_argument("schema", help="Input XSD file")
    add_policy_arguments(prune_parser)
    prune_parser.add_argument(
        "--root-element",
        default=None,
        help="Override the global root element used for reachability.",
    )
    prune_parser.add_argument(
        "--keep",
        action="append",
        default=[],
        metavar="NAME",
        help="Keep an additional public component by name; may be repeated.",
    )
    prune_parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the migration. Without this flag, only print the plan.",
    )
    add_write_arguments(prune_parser)
    prune_parser.set_defaults(handler=command_prune_unused)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    try:
        policy = resolve_policy(args)
        return int(args.handler(args, policy))
    except (PolicyError, SchemaToolError) as exc:
        print(f"cpacs-schema: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("cpacs-schema: interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
