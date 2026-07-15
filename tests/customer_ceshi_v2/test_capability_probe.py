from agents.customer_ceshi_v2.capabilities import probe_text_capabilities


class FullyCapableClient:
    def invoke(self, prompt, **kwargs):
        return "ok"

    def bind_tools(self, tools):
        return self

    def stream(self, prompt):
        yield "ok"


def test_capability_probe_uses_client_behavior_not_model_name():
    capabilities = probe_text_capabilities(FullyCapableClient())

    assert capabilities.text is True
    assert capabilities.thinking is True
    assert capabilities.native_tool_calls is True
    assert capabilities.json_output is True
    assert capabilities.streaming is True
