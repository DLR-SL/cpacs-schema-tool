# Origin and extraction scope

The initial implementation is derived from the CPACS repository:

- Repository: `DLR-SL/CPACS`
- Original implementation: `scripts/schema_tool.py`
- Original policy: `scripts/schema_rules.toml`
- Extraction baseline: CPACS `develop`, inspected on 2026-08-05
- Original replacement commit: `2f9ce3d45e7af6c97287ddc37a721da00e3e0baa`

The package keeps the CPACS-specific diagnostic identifiers and standard policy.
Project-specific deviations are intended to be small override files rather than
copies of the complete standard policy.
