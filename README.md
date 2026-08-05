# CPACS Schema Tool

`cpacs-schema-tool` is the shared formatter, checker, linter, and explicit
migration utility for CPACS XML Schema files. It is intended for the official
CPACS repository, TiGL, and project-internal CPACS schema instances.

The package ships the **CPACS Standard Schema Rules**. Projects use those rules
without copying a configuration file and may layer small, explicit overrides on
top when necessary.

## Commands

```bash
cpacs-schema format schema/cpacs_schema.xsd --in-place
cpacs-schema check schema/cpacs_schema.xsd
cpacs-schema lint schema/cpacs_schema.xsd
cpacs-schema rename-types schema/cpacs_schema.xsd
cpacs-schema prune-unused schema/cpacs_schema.xsd
```

The commands deliberately distinguish representation-preserving formatting from
schema-changing migrations:

- `format` normalizes ordering, attributes, whitespace, and redundant occurrence
  defaults.
- `check` verifies canonical formatting and XSD compilation.
- `lint` checks CPACS conventions, references, reachability, prefixes, and XSD
  validity.
- `rename-types` produces a dry-run plan unless `--apply` is supplied.
- `prune-unused` produces a dry-run plan unless `--apply` is supplied.

Writes are atomic. Existing files are overwritten only with `--in-place`; an
optional `--backup` creates `<schema>.bak`.

## Installation

Development installation:

```bash
python -m pip install -e ".[dev]"
python -m pytest
```

Using Conda:

```bash
conda create -n cpacs-schema -c conda-forge python=3.10 pip git
conda activate cpacs-schema
python -m pip install "cpacs-schema-tool @ git+https://github.com/DLR-SL/cpacs-schema-tool.git@v0.1.1"
cpacs-schema --version
```

Using Pixi:

```bash
pixi run check
```

Until a package release is published, a consumer can pin the Git repository:

```toml
[pypi-dependencies]
cpacs-schema-tool = {
    git = "https://github.com/DLR-SL/cpacs-schema-tool.git",
    rev = "<full-commit-sha>"
}
```

After publishing a release:

```toml
[pypi-dependencies]
cpacs-schema-tool = "==0.1.0"
```

## Built-in policy

Running a command without `--rules` uses the bundled standard policy:

```bash
cpacs-schema lint schema/cpacs_schema.xsd
```

The bundled policy defines, among other things:

- the `cpacs` root element and `cpacsType` root type;
- canonical top-level declaration and attribute ordering;
- CPACS base-type ordering;
- type naming rules;
- reference and reachability checks;
- XSD/XLink prefix conventions;
- severities for diagnostics `CPACS001` through `CPACS011`.

The policy has its own metadata:

```toml
[policy]
name = "CPACS Standard Schema Rules"
version = "1.0"
```

## Partial project overrides

`--rules` applies a partial TOML file over the bundled CPACS defaults. The option
may be repeated; later files take precedence.

```bash
cpacs-schema lint project_schema.xsd \
    --rules project_schema_overrides.toml
```

Example:

```toml
[reachability]
keep_add = ["projectMetadataType"]

[naming.types]
exceptions_add = ["LegacyProjectType"]

[rules.CPACS009]
severity = "error"
```

Tables are merged recursively. Scalar values and ordinary arrays replace the
corresponding defaults. For array-valued settings, `_add` and `_remove` modify
the existing standard list without copying it:

```toml
[reachability]
keep_add = ["publicProjectType"]
keep_remove = ["obsoletePublicType"]
```

A complete independent policy can be supplied with `--replace-rules`. It must
contain every required table and is mutually exclusive with `--rules`.

```bash
cpacs-schema lint another_schema.xsd \
    --replace-rules complete_policy.toml
```

The effective merged policy can be inspected reproducibly:

```bash
cpacs-schema lint schema.xsd --print-effective-rules
```

## Recommended CPACS integration

The CPACS repository no longer needs its own copy of `schema_tool.py` or
`schema_rules.toml`:

```toml
[tasks]
test-schema = "cpacs-schema check schema/cpacs_schema.xsd"
lint-schema = "cpacs-schema lint schema/cpacs_schema.xsd"
format-schema = "cpacs-schema format schema/cpacs_schema.xsd --in-place"
test-examples = "python -m pytest scripts/tests/test_examples.py -v"
check = { depends-on = ["test-schema", "lint-schema", "test-examples"] }
```

The CPACS repository retains the actual XSD, its examples, and integration tests.
The tool repository owns the implementation, standard rules, unit tests, and
releases.

## Policy and tool versioning

Consumer repositories should pin a released tool version or full Git commit.
Changes that alter canonical formatting or turn formerly accepted constructs
into errors must be documented in the changelog. Explicit rename mappings are
best supplied as release-specific override files rather than accumulated in the
permanent standard policy.

## License

Apache License 2.0. See `LICENSE` and `NOTICE`.
