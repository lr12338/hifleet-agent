from pathlib import Path


def test_responses_runtime_does_not_import_production_customer_support_modules():
    root = Path(__file__).parents[2] / "src" / "agents" / "customer_ceshi_responses"
    prohibited = (
        "agents.customer_support_router",
        "agents.customer_support_understanding",
        "agents.customer_support_evidence_guard",
        "LightweightCustomerSupportState",
    )
    source = "\n".join(path.read_text(encoding="utf-8") for path in root.glob("*.py"))
    assert not any(item in source for item in prohibited)
