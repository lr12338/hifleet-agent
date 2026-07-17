import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from skills.knowledge_qa.local_kb_runtime import reset_local_kb_index, search_local_kb_structured


def test_local_kb_indexes_product_guidance_sections_for_product_operations():
    reset_local_kb_index()
    result = search_local_kb_structured("HiFleet 船队管理 创建船队 操作步骤", top_k=8)

    sources = [item["source"] for item in result["items"]]
    titles = [item["title"] for item in result["items"]]

    assert any("产品指导文档" in source for source in sources)
    assert any("创建船队" in title for title in titles)
    assert not result["can_answer"] or any("创建船队" in title for title in titles)
