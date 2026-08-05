# Changelog

## 0.1.1 - 2026-08-05

- Fix Ruff violations in the Python sources and restore a successful
  Linux/Windows CI matrix.
- Apply consistent source formatting without changing the schema-tool
  behavior.

## 0.1.0 - 2026-08-05

- Extract the CPACS schema formatter, checker, linter, and migration workflow
  into an installable Python package.
- Bundle the official CPACS standard schema rules as package resources.
- Add layered partial overrides through repeatable `--rules` options.
- Add `--replace-rules` for fully independent policies.
- Add effective-policy output for reproducible CI diagnostics.
- Add unit tests and Linux/Windows CI.
