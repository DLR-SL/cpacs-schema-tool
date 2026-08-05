# Migration of `DLR-SL/CPACS`

This document describes the intended first consumer migration after publishing
`cpacs-schema-tool`.

## 1. Publish and pin the tool

Create the repository `DLR-SL/cpacs-schema-tool`, push this initial project, and
create a `v0.1.0` tag after review.

During the review phase, pin a full Git commit in the CPACS `pixi.toml`:

```toml
[pypi-dependencies]
cpacs-schema-tool = {
    git = "https://github.com/DLR-SL/cpacs-schema-tool.git",
    rev = "<full-commit-sha>"
}
```

After the package is published, replace the Git dependency with a released
version:

```toml
[pypi-dependencies]
cpacs-schema-tool = "==0.1.0"
```

Keep `lxml` available explicitly in CPACS only when other CPACS tests or scripts
use it directly. The schema tool itself declares `lxml` as a package dependency.

## 2. Replace Pixi tasks

```toml
[tasks]
test-schema = "cpacs-schema check schema/cpacs_schema.xsd"
lint-schema = "cpacs-schema lint schema/cpacs_schema.xsd"
test-examples = "python -m pytest scripts/tests/test_examples.py -v"
format-schema = "cpacs-schema format schema/cpacs_schema.xsd --in-place"
check = { depends-on = ["test-schema", "lint-schema", "test-examples"] }
```

No `--rules` argument is required for the official CPACS schema because the
standard policy is bundled with the package.

## 3. Remove duplicated implementation and policy

Delete from CPACS after the new dependency has passed CI:

```text
scripts/schema_tool.py
scripts/schema_rules.toml
scripts/license.txt
```

The schema formatter preserves the existing preamble of
`schema/cpacs_schema.xsd`; the separate script-local header file is therefore no
longer needed.

Keep:

```text
scripts/tests/test_examples.py
schema/cpacs_schema.xsd
examples/*.xml
```

Those files are CPACS integration assets rather than schema-tool unit tests.

## 4. Keep convenience wrappers

A Windows wrapper can remain minimal:

```bat
@echo off
pixi run format-schema
```

## 5. Verify migration

Run on Linux and Windows:

```bash
pixi install
pixi run --frozen check
```

Before merging, compare the output of the old and new formatter once on the
current CPACS schema. Any difference must be reviewed because canonical
formatting is part of the repository contract.

## 6. Project-specific policies

TiGL or internal projects should use the same package version and add only a
small override where needed:

```bash
cpacs-schema lint schema.xsd --rules project_overrides.toml
```

Do not copy the complete standard `schema_rules.toml` into consumer repositories.
