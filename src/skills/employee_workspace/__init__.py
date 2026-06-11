"""Internal employee workspace skill."""
from skills.employee_workspace.tools import (
    download_public_file_to_artifact,
    inspect_tabular_file,
    run_sandboxed_python,
)

SKILL_TOOLS = [download_public_file_to_artifact, inspect_tabular_file, run_sandboxed_python]
