# Skill: employee-workspace

## Scope
Internal employee-only file processing and data analysis capability.

Use this skill for:
- Inspecting CSV/XLSX files.
- Running small Python analysis jobs in a restricted sandbox.
- Producing text summaries and controlled artifacts for internal review.

Do not use this skill for external customer sessions.

## Tools

### inspect_tabular_file
Reads a CSV or XLSX file from the allowed workspace area and returns columns, shape, and a small preview.

### run_sandboxed_python
Runs short Python snippets in an isolated temporary working directory with a timeout.

Rules:
- Use it for calculations, table transformations, and report generation.
- Keep code small and deterministic.
- Write generated artifacts only under the provided sandbox output directory.
- Never attempt to read secrets, service config, SSH keys, or arbitrary host paths.
