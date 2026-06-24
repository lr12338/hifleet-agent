import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agents.profiles import set_current_agent_profile
from coze_coding_utils.log.write_log import request_context
from coze_coding_utils.runtime_ctx.context import new_context
from skills.knowledge_admin.tools import upsert_local_kb_entry


def _set_ctx(profile: str = "customer_support"):
    ctx = new_context(method="test_kb_admin")
    request_context.set(ctx)
    set_current_agent_profile(profile)


def test_customer_support_can_upsert_authorized_kb_entry(tmp_path, monkeypatch):
    kb_path = tmp_path / "kb.jsonl"
    kb_path.write_text(
        json.dumps(
            {
                "id": "kb_001",
                "category": "产品功能",
                "intent": "existing",
                "question": "DTU是什么？",
                "answer": "DTU 是数据传输单元。",
                "keywords": ["DTU"],
                "sources": [],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HIFLEET_KB_UPDATE_KEY", "hifleetdataupdate")
    monkeypatch.setenv("HIFLEET_KB_JSONL_PATH", str(kb_path))
    _set_ctx("customer_support")

    raw_text = (
        "更新知识库："
        "HiFleet 海图图标识别特征库：紫色点圈，中心有灰绿色点，为泊位图标。"
        "详情链接参考 https://www.hifleet.com/wp/communities/fleet/haitutubiaoshuoming#post-305"
        "\nkey: hifleetdataupdate"
    )
    result = json.loads(upsert_local_kb_entry.invoke({"raw_text": raw_text}))

    assert result["ok"] is True
    assert result["status"] == "upserted"
    lines = [json.loads(line) for line in kb_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 2
    assert lines[-1]["id"] == result["id"]
    assert lines[-1]["category"] == "海图图标"
    assert lines[-1]["intent"] == "chart_symbol_knowledge"
    assert "泊位图标" in lines[-1]["answer"]
    assert "hifleetdataupdate" not in json.dumps(lines[-1], ensure_ascii=False)
    assert "hifleetdataupdate" not in json.dumps(result, ensure_ascii=False)
    assert "https://www.hifleet.com/wp/communities/fleet/haitutubiaoshuoming#post-305" in lines[-1]["sources"]


def test_kb_upsert_requires_explicit_command_and_key(tmp_path, monkeypatch):
    kb_path = tmp_path / "kb.jsonl"
    kb_path.write_text("", encoding="utf-8")
    monkeypatch.setenv("HIFLEET_KB_UPDATE_KEY", "hifleetdataupdate")
    monkeypatch.setenv("HIFLEET_KB_JSONL_PATH", str(kb_path))
    _set_ctx("customer_support")

    no_command = json.loads(upsert_local_kb_entry.invoke({"raw_text": "你答错了，应该这样回答。"}))
    assert no_command["status"] == "rejected"
    assert no_command["reason"] == "missing_explicit_kb_update_command"

    missing_key = json.loads(upsert_local_kb_entry.invoke({"raw_text": "添加知识库：问题：测试\n答案：这是一个足够长的标准答案内容。"}))
    assert missing_key["status"] == "rejected"
    assert missing_key["reason"] == "invalid_or_missing_kb_update_key"
    assert kb_path.read_text(encoding="utf-8") == ""

    model_split_key = json.loads(
        upsert_local_kb_entry.invoke(
            {
                "raw_text": "更新知识库：问题：泊位图标是什么\n答案：HiFleet 海图中紫色点圈且中心有灰绿色点，可识别为泊位图标。",
                "question": "泊位图标是什么",
                "answer": "HiFleet 海图中紫色点圈且中心有灰绿色点，可识别为泊位图标。",
            }
        )
    )
    assert model_split_key["status"] == "rejected"
    assert model_split_key["reason"] == "invalid_or_missing_kb_update_key"


def test_kb_upsert_rejects_header_key(tmp_path, monkeypatch):
    from utils.context_headers import ensure_context_headers

    kb_path = tmp_path / "kb.jsonl"
    kb_path.write_text("", encoding="utf-8")
    monkeypatch.setenv("HIFLEET_KB_UPDATE_KEY", "hifleetdataupdate")
    monkeypatch.setenv("HIFLEET_KB_JSONL_PATH", str(kb_path))
    ctx = new_context(method="test_kb_admin")
    headers = ensure_context_headers(ctx)
    headers["x-agent-profile"] = "customer_support"
    headers["x-kb-update-key"] = "hifleetdataupdate"
    request_context.set(ctx)
    set_current_agent_profile("customer_support")

    result = json.loads(
        upsert_local_kb_entry.invoke(
            {
                "raw_text": "添加知识库：\n问题：泊位图标是什么\n答案：HiFleet 海图中紫色点圈且中心有灰绿色点，可识别为泊位图标。",
            }
        )
    )

    assert result["ok"] is False
    assert result["status"] == "rejected"
    assert result["reason"] == "header_key_not_supported"
    assert kb_path.read_text(encoding="utf-8") == ""


def test_kb_upsert_rejects_duplicate_question(tmp_path, monkeypatch):
    kb_path = tmp_path / "kb.jsonl"
    existing = {
        "id": "kb_001",
        "category": "平台操作",
        "intent": "platform_operation",
        "question": "怎么绘制区域标注",
        "answer": "在主海图页面右上角点击标注，选择区域标注后绘制并保存。",
        "keywords": ["区域标注", "标注", "保存"],
        "sources": [],
    }
    kb_path.write_text(json.dumps(existing, ensure_ascii=False) + "\n", encoding="utf-8")
    monkeypatch.setenv("HIFLEET_KB_UPDATE_KEY", "hifleetdataupdate")
    monkeypatch.setenv("HIFLEET_KB_JSONL_PATH", str(kb_path))
    _set_ctx("customer_ceshi")

    result = json.loads(
        upsert_local_kb_entry.invoke(
            {
                "raw_text": "添加知识库：key: hifleetdataupdate\n问题：怎么绘制区域标注\n答案：打开主海图右上角标注按钮，选择区域标注，绘制后点击保存完成。",
            }
        )
    )

    assert result["status"] == "duplicate"
    assert result["reason"] == "same_question"
    assert len([line for line in kb_path.read_text(encoding="utf-8").splitlines() if line.strip()]) == 1


def test_kb_upsert_structures_short_chart_symbol_fact(tmp_path, monkeypatch):
    kb_path = tmp_path / "kb.jsonl"
    kb_path.write_text("", encoding="utf-8")
    monkeypatch.setenv("HIFLEET_KB_UPDATE_KEY", "hifleetdataupdate")
    monkeypatch.setenv("HIFLEET_KB_JSONL_PATH", str(kb_path))
    _set_ctx("customer_support")

    result = json.loads(
        upsert_local_kb_entry.invoke(
            {
                "raw_text": "更新知识库：hifleet海图标识：紫色点圈，中心有灰绿色点，为 泊位图标\nkey: hifleetdataupdate",
            }
        )
    )

    assert result["ok"] is True
    rows = [json.loads(line) for line in kb_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert rows[0]["category"] == "海图图标"
    assert rows[0]["intent"] == "chart_symbol_knowledge"
    assert rows[0]["question"] == "HiFleet 海图中紫色点圈、中心有灰绿色点是什么图标？"
    assert rows[0]["answer"] == "HiFleet 海图中紫色点圈且中心有灰绿色点，可识别为泊位图标。"
    assert rows[0]["keywords"][:5] == ["海图标识", "泊位图标", "紫色点圈", "灰绿色点", "图标识别"]


def test_kb_upsert_accepts_loose_update_command_punctuation(tmp_path, monkeypatch):
    kb_path = tmp_path / "kb.jsonl"
    kb_path.write_text("", encoding="utf-8")
    monkeypatch.setenv("HIFLEET_KB_UPDATE_KEY", "hifleetdataupdate")
    monkeypatch.setenv("HIFLEET_KB_JSONL_PATH", str(kb_path))
    _set_ctx("customer_support")

    for raw_text in (
        "更新知识库，泊位图标：紫色点圈，中心有灰绿色点，为泊位图标\nkey: hifleetdataupdate",
        "更新知识库\n泊位图标：紫色点圈，中心有灰绿色点，为泊位图标\nkey: hifleetdataupdate",
        "更新知识库，key: hifleetdataupdate：\n泊位图标：紫色点圈，中心有灰绿色点，为泊位图标",
    ):
        result = json.loads(upsert_local_kb_entry.invoke({"raw_text": raw_text}))
        assert result["status"] in {"upserted", "duplicate"}


def test_kb_upsert_splits_chart_symbol_mapping_rows(tmp_path, monkeypatch):
    kb_path = tmp_path / "kb.jsonl"
    kb_path.write_text("", encoding="utf-8")
    monkeypatch.setenv("HIFLEET_KB_UPDATE_KEY", "hifleetdataupdate")
    monkeypatch.setenv("HIFLEET_KB_JSONL_PATH", str(kb_path))
    _set_ctx("customer_support")

    raw_text = """更新知识库，：
# HiFleet 海图图标识别特征库
图标名称：图标描述
安全水域浮标：红色实心圆圈，内部全红色填充，中心黑色圆点
泊位图标：紫色点线空心圆圈，中心灰绿色实心小点
锚泊区：紫色虚线围成闭合多边形，内部带锚形符号
key: hifleetdataupdate"""
    result = json.loads(upsert_local_kb_entry.invoke({"raw_text": raw_text}))

    assert result["ok"] is True
    assert result["status"] == "batch_upserted"
    assert result["inserted_count"] == 3
    rows = [json.loads(line) for line in kb_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) == 3
    assert {row["category"] for row in rows} == {"海图图标"}
    assert {row["intent"] for row in rows} == {"chart_symbol_knowledge"}
    assert rows[1]["question"] == "HiFleet 海图中“泊位图标”是什么图标/有什么识别特征？"
    assert rows[1]["answer"] == "HiFleet 海图中，泊位图标的识别特征是：紫色点线空心圆圈，中心灰绿色实心小点。"
    assert "hifleetdataupdate" not in json.dumps(rows, ensure_ascii=False)


def test_kb_upsert_batch_skips_duplicates_and_writes_new_rows(tmp_path, monkeypatch):
    kb_path = tmp_path / "kb.jsonl"
    existing = {
        "id": "kb_001",
        "category": "海图图标",
        "intent": "chart_symbol_knowledge",
        "question": "HiFleet 海图中“泊位图标”是什么图标/有什么识别特征？",
        "answer": "HiFleet 海图中，泊位图标的识别特征是：紫色点线空心圆圈，中心灰绿色实心小点。",
        "keywords": ["泊位图标", "海图标识"],
        "sources": [],
    }
    kb_path.write_text(json.dumps(existing, ensure_ascii=False) + "\n", encoding="utf-8")
    monkeypatch.setenv("HIFLEET_KB_UPDATE_KEY", "hifleetdataupdate")
    monkeypatch.setenv("HIFLEET_KB_JSONL_PATH", str(kb_path))
    _set_ctx("customer_support")

    raw_text = """更新知识库：
# HiFleet 海图图标识别特征库
泊位图标：紫色点线空心圆圈，中心灰绿色实心小点
锚泊区：紫色虚线围成闭合多边形，内部带锚形符号
key: hifleetdataupdate"""
    result = json.loads(upsert_local_kb_entry.invoke({"raw_text": raw_text}))

    assert result["status"] == "partial"
    assert result["inserted_count"] == 1
    assert result["duplicate_count"] == 1
    rows = [json.loads(line) for line in kb_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) == 2
    assert rows[-1]["id"] == "kb_002"
