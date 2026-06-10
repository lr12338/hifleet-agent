"""Internal employee workspace skill."""
from skills.employee_workspace.tools import inspect_tabular_file, run_sandboxed_python

SKILL_TOOLS = [inspect_tabular_file, run_sandboxed_python]
