from langgraph.checkpoint.memory import MemorySaver

from agents.customer_ceshi_v2.builder import CHECKPOINT_NAMESPACE, _NamespacedGraph


class RecordingGraph:
    def __init__(self):
        self.config = None

    def invoke(self, input, config, **kwargs):
        self.config = config
        return {"ok": True}


def test_v2_forces_its_own_checkpoint_namespace():
    graph = RecordingGraph()
    scoped = _NamespacedGraph(graph)

    assert scoped.invoke({}, {"configurable": {"thread_id": "same-session", "checkpoint_ns": "other"}}) == {"ok": True}
    assert graph.config["configurable"]["checkpoint_ns"] == CHECKPOINT_NAMESPACE
    assert graph.config["configurable"]["thread_id"] == f"{CHECKPOINT_NAMESPACE}:same-session"
