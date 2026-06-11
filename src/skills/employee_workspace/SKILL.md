# Skill: employee-workspace

## Scope
Internal employee-only file processing and data analysis capability.

Use this skill for:
- Downloading public CSV/XLS/XLSX links into the employee artifact area.
- Inspecting CSV/XLSX files.
- Running small Python analysis jobs in a Docker sibling-container sandbox.
- Producing text summaries and controlled artifacts for internal review.

Do not use this skill for external customer sessions.

## Tools

### download_public_file_to_artifact
Downloads a public CSV/XLS/XLSX URL into the artifact directory and returns the local path.

### inspect_tabular_file
Reads a CSV or XLSX file from the allowed workspace area and returns columns, shape, dtypes, missing-values, and a small preview.

### run_sandboxed_python
Runs short Python snippets in a restricted Docker container with AST pre-check, timeout, optional artifact validation, and sandbox-local INPUT_FILE mapping.

Rules:
- Always inspect the target file before writing Python code.
- Only use real column names returned by `inspect_tabular_file`.
- Use it for calculations, table transformations, and report generation.
- Keep code small and deterministic.
- Write generated artifacts only under `ARTIFACT_DIR`.
- Include enough `print(...)` output for deterministic debugging.
- Never attempt to read secrets, service config, SSH keys, or arbitrary host paths.
