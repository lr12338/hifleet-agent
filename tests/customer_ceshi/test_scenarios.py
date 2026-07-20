from agents.customer_ceshi_responses.scenarios import classify


def test_static_update_contract_excludes_direct_write_tools():
    contract = classify("怎么在平台手动上传目的港 ETA")
    assert contract.name == "static_update"
    assert "update_ship_static_info" not in contract.allowed_tools
    assert "prepare_ship_update" in contract.allowed_tools


def test_symbol_contract_requires_perception_and_evidence_tools():
    contract = classify("这个海图符号是什么意思", has_media=True)
    assert contract.name == "multimodal_symbol"
    assert {"inspect_media", "local_kb_search"}.issubset(contract.allowed_tools)


def test_visual_symbol_contract_covers_chart_lines_and_circles():
    assert classify("图上紫色的波浪线是指什么", has_media=True).name == "multimodal_symbol"
    assert classify("图中的小圈圈是什么意思", has_media=True).name == "multimodal_symbol"
