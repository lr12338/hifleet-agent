from types import SimpleNamespace

from agents import agent


def test_customer_ceshi_uses_v2_builder_without_touching_support(monkeypatch):
    profile = SimpleNamespace(profile_id="customer_ceshi")
    sentinel = object()
    monkeypatch.setattr(agent, "_resolve_agent_profile", lambda ctx: profile)
    monkeypatch.setattr(agent, "_load_llm_config", lambda path: {"config": {}})
    monkeypatch.setattr(agent, "_resolve_intent_hint", lambda ctx, explicit_intent: "")
    monkeypatch.setattr("agents.customer_ceshi_v2.build_customer_ceshi_v2_agent", lambda *args, **kwargs: sentinel)
    monkeypatch.setattr(agent, "_build_lightweight_customer_support_agent", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("production builder must not be used")))

    assert agent.build_agent() is sentinel


def test_customer_support_keeps_existing_builder(monkeypatch):
    profile = SimpleNamespace(profile_id="customer_support")
    sentinel = object()
    monkeypatch.setattr(agent, "_resolve_agent_profile", lambda ctx: profile)
    monkeypatch.setattr(agent, "_load_llm_config", lambda path: {"config": {}})
    monkeypatch.setattr(agent, "_resolve_intent_hint", lambda ctx, explicit_intent: "")
    monkeypatch.setattr(agent, "_build_lightweight_customer_support_agent", lambda *args, **kwargs: sentinel)

    assert agent.build_agent() is sentinel


def test_customer_ceshi_selects_native_runtime_only_when_explicitly_enabled(monkeypatch):
    profile = SimpleNamespace(profile_id="customer_ceshi", skills=[])
    sentinel = object()
    monkeypatch.setattr(agent, "_resolve_agent_profile", lambda ctx: profile)
    monkeypatch.setattr(agent, "_load_llm_config", lambda path: {"config": {"customer_ceshi_runtime": {"mode": "chat_function_calling"}}})
    monkeypatch.setattr(agent, "_resolve_intent_hint", lambda ctx, explicit_intent: "")
    monkeypatch.setattr("agents.customer_ceshi_responses.build_customer_ceshi_responses_agent", lambda *args, **kwargs: sentinel)
    monkeypatch.setattr("agents.customer_ceshi_v2.build_customer_ceshi_v2_agent", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("legacy builder must not be used")))

    assert agent.build_agent() is sentinel
