from agents.customer_ceshi_responses.ship_updates import PositionNormalizer, ShipIdentityNormalizer, ShipUpdateDraftStore, StaticFieldNormalizer, TimeNormalizer
from agents.customer_ceshi_responses.builder import COMMIT_SHIP_UPDATE_TOOL_NAME, PREPARE_SHIP_UPDATE_TOOL_NAME, NativeToolRuntime


def test_position_normalizer_handles_degree_minutes_and_compact_coordinates():
    value = PositionNormalizer().normalize("位置：19°40.094′ N 038°48.771′ E")
    assert round(value["longitude"], 6) == 38.81285
    assert round(value["latitude"], 6) == 19.668233
    assert value["confidence"] == "deterministic"


def test_position_normalizer_handles_prefix_and_labelled_decimal_coordinates():
    prefixed = PositionNormalizer().normalize("E121 41.23 N39 00.41")
    labelled = PositionNormalizer().normalize("经度：121.687166 纬度：39.006833")
    assert round(prefixed["longitude"], 6) == 121.687167
    assert round(prefixed["latitude"], 6) == 39.006833
    assert labelled["confidence"] == "deterministic"


def test_position_normalizer_handles_hyphenated_degree_minutes_without_quote():
    value = PositionNormalizer().normalize("位置22-42.9n 068-58.1e")
    assert round(value["longitude"], 6) == 68.968333
    assert round(value["latitude"], 6) == 22.715


def test_identity_and_static_field_normalizers_reject_placeholders():
    identity = ShipIdentityNormalizer().normalize("更新静态信息，MMSI：414718000")
    fields = StaticFieldNormalizer().normalize("目的港：PIRAEUS，ETA：2026-07-18 10:00，船旗：--，吃水：12.3")
    assert identity["mmsi"] == "414718000"
    assert fields["fields"] == {"destination": "PIRAEUS", "eta": "2026-07-18 10:00", "draft": "12.3"}
    assert "flag" in fields["invalid_fields"]


def test_static_field_normalizer_handles_compact_destination_eta_formats():
    compact = StaticFieldNormalizer().normalize("更新MMSI：538005903目的港/ETADANGJIN / 2026-07-08 13:00，吃水14.4 m")
    after_mmsi = StaticFieldNormalizer().normalize("更新目的港，mmsi：413373860，ZHENHAI / 2026-07-05 11:30 (UTC)")
    assert compact["fields"]["destination"] == "DANGJIN"
    assert compact["fields"]["eta"] == "2026-07-08 13:00"
    assert after_mmsi["fields"]["destination"] == "ZHENHAI"
    assert after_mmsi["fields"]["eta"] == "2026-07-05 11:30 (UTC)"


def test_time_normalizer_never_silently_corrects_five_digit_year():
    value = TimeNormalizer().normalize("22026-07-04 1536")
    assert value["value"].startswith("2026-07-04 15:36")
    assert value["requires_confirmation"] is True


def test_draft_is_session_scoped_and_requires_position_fields():
    store = ShipUpdateDraftStore()
    draft = store.prepare(session_key="customer_ceshi:t:u:s", operation_type="position_update", target={"mmsi": "730285526"}, fields={"longitude": 121.6}, field_sources={"longitude": "current_turn_text"})
    assert draft.missing_fields == ["latitude", "updatetime"]
    assert store.get("customer_ceshi:t:u:s").draft_id == draft.draft_id
    assert store.get("customer_ceshi:t:other:s") is None


def test_draft_survives_store_recreation(tmp_path):
    path = tmp_path / "drafts.json"
    first = ShipUpdateDraftStore(path)
    created = first.prepare(session_key="customer_ceshi:t:u:s", operation_type="static_update", target={"mmsi": "730285526"}, fields={"destination": "PIRAEUS"}, field_sources={"destination": "current_turn_text"})
    restored = ShipUpdateDraftStore(path).get("customer_ceshi:t:u:s")
    assert restored is not None
    assert restored.draft_id == created.draft_id
    assert restored.fields["destination"] == "PIRAEUS"


def test_unstructured_write_reply_is_never_interpreted_as_success():
    observation = NativeToolRuntime._write_observation("upload_ship_position", "上传成功", {"mmsi": "730285526"})
    assert observation.status == "upstream_error"
    assert observation.data["adapter_status"] == "unknown"


def test_prepare_then_commit_is_session_scoped_and_never_writes_without_adapter():
    runtime = NativeToolRuntime(client=object(), registry=type("Registry", (), {"_tools": {}})(), config={}, mode="chat_function_calling")
    prepared = runtime._draft_operation(PREPARE_SHIP_UPDATE_TOOL_NAME, {"operation_type": "position_update", "mmsi": "730285526", "longitude": "121°41.23′ E", "latitude": "39°00.41′ N", "updatetime": "2026-07-04 1443 (UTC+8)"}, "customer_ceshi:t:u:s")
    assert prepared.status == "success"
    committed = runtime._draft_operation(COMMIT_SHIP_UPDATE_TOOL_NAME, {"draft_id": prepared.data["draft_id"], "confirmed": True}, "customer_ceshi:t:u:s")
    assert committed.status == "forbidden"
    assert runtime._draft_operation(COMMIT_SHIP_UPDATE_TOOL_NAME, {"draft_id": prepared.data["draft_id"], "confirmed": True}, "customer_ceshi:t:other:s").status == "invalid_input"


def test_dry_run_commit_is_accepted_but_not_production_success(tmp_path):
    runtime = NativeToolRuntime(client=object(), registry=type("Registry", (), {"_tools": {}})(), config={"customer_ceshi_runtime": {"direct_updates": {"dry_run": True, "draft_store_path": str(tmp_path / "drafts.json")}}}, mode="chat_function_calling")
    prepared = runtime._draft_operation(PREPARE_SHIP_UPDATE_TOOL_NAME, {"operation_type": "static_update", "mmsi": "730285526", "fields": {"destination": "PIRAEUS"}}, "customer_ceshi:t:u:s")
    result = runtime._draft_operation(COMMIT_SHIP_UPDATE_TOOL_NAME, {"draft_id": prepared.data["draft_id"], "confirmed": True}, "customer_ceshi:t:u:s")
    assert result.status == "partial"
    assert result.data["adapter_status"] == "accepted"
    assert result.data["dry_run"] is True


def test_same_session_confirmation_does_not_require_user_visible_draft_id(tmp_path):
    runtime = NativeToolRuntime(client=object(), registry=type("Registry", (), {"_tools": {}})(), config={"customer_ceshi_runtime": {"direct_updates": {"dry_run": True, "draft_store_path": str(tmp_path / "drafts.json")}}}, mode="chat_function_calling")
    runtime._draft_operation(PREPARE_SHIP_UPDATE_TOOL_NAME, {"operation_type": "static_update", "mmsi": "730285526", "fields": {"destination": "PIRAEUS"}}, "customer_ceshi:t:u:s")
    result = runtime._draft_operation(COMMIT_SHIP_UPDATE_TOOL_NAME, {"confirmed": True}, "customer_ceshi:t:u:s")
    assert result.status == "partial"


def test_accepted_dry_run_cannot_be_rendered_as_update_success():
    answer, guard = NativeToolRuntime._guard("更新成功。", [{"status": "partial", "capability": COMMIT_SHIP_UPDATE_TOOL_NAME, "data": {"adapter_status": "accepted", "dry_run": True}}])
    assert guard == "accepted_write_not_confirmed"
    assert "不能确认" in answer


def test_confirmation_normalizes_terminal_chinese_punctuation():
    assert "确认。".strip().strip("。！？!?，,；;") == "确认"


def test_text_position_preflight_prepares_deterministic_draft(tmp_path):
    runtime = NativeToolRuntime(client=object(), registry=type("Registry", (), {"_tools": {}})(), config={"customer_ceshi_runtime": {"direct_updates": {"draft_store_path": str(tmp_path / "drafts.json")}}}, mode="responses", responses_client=object())
    observation = runtime._prepare_text_position_update("更新船位，MMSI 414718000，更新时间：2026-07-04 1443 (UTC+8)，经度：121°41.23′ E，纬度：39°00.41′ N", "customer_ceshi:position")
    assert observation is not None
    assert observation.status == "success"
    assert round(observation.data["fields"]["longitude"], 6) == 121.687167
    assert round(observation.data["fields"]["latitude"], 6) == 39.006833
    assert observation.data["fields"]["updatetime"] == "2026-07-04 14:43:00 UTC+8"


def test_text_position_preflight_rejects_five_digit_year(tmp_path):
    runtime = NativeToolRuntime(client=object(), registry=type("Registry", (), {"_tools": {}})(), config={"customer_ceshi_runtime": {"direct_updates": {"draft_store_path": str(tmp_path / "drafts.json")}}}, mode="responses", responses_client=object())
    observation = runtime._prepare_text_position_update("更新船位，MMSI 414718000，更新时间：22026-07-04 1443，经度：121°41.23′ E，纬度：39°00.41′ N", "customer_ceshi:position")
    assert observation is not None
    assert observation.status == "invalid_input"
    assert "五位年份" in observation.suggested_fix


def test_text_static_preflight_prepares_only_current_turn_fields(tmp_path):
    runtime = NativeToolRuntime(client=object(), registry=type("Registry", (), {"_tools": {}})(), config={"customer_ceshi_runtime": {"direct_updates": {"draft_store_path": str(tmp_path / "drafts.json")}}}, mode="responses", responses_client=object())
    observation = runtime._prepare_text_update("更新静态信息，MMSI：414718000，目的港：PIRAEUS，ETA：2026-07-18 10:00，船旗：--", "customer_ceshi:static")
    assert observation is not None
    assert observation.status == "success"
    assert observation.data["fields"] == {"destination": "PIRAEUS", "eta": "2026-07-18 10:00"}


def test_position_normalizer_handles_dms_with_seconds_and_labelled_newlines():
    text = "纬度\n39°01′55″ N\n经度\n121°42′55″ E"
    value = PositionNormalizer().normalize(text)
    assert round(value["latitude"], 6) == 39.031944
    assert round(value["longitude"], 6) == 121.715278
    assert value["confidence"] == "deterministic"


def test_position_normalizer_handles_labelled_dms_without_hemisphere():
    value = PositionNormalizer().normalize("经度：121°42′55″  纬度：39°01′55″")
    assert round(value["longitude"], 6) == 121.715278
    assert round(value["latitude"], 6) == 39.031944
    assert value["confidence"] == "deterministic"


def test_static_field_normalizer_handles_newline_labelled_hifleet_paste():
    text = "更新船位，更新于2026-07-06 14:13:00 UTC+8\nMMSI\n730285526\nIMO\n-\n船旗\n哥伦比亚\n类型\n未知类型船舶\n吃水\n-\nETA\n-"
    fields = StaticFieldNormalizer().normalize(text)
    assert fields["fields"] == {"flag": "哥伦比亚", "ship_type": "未知类型船舶"}
    assert {"imo", "draft", "eta"}.issubset(set(fields["invalid_fields"]))


def test_time_normalizer_handles_utc_plus_eight_suffix():
    value = TimeNormalizer().normalize("2026-07-06 14:13:00 UTC+8")
    assert value["value"] == "2026-07-06 14:13:00 UTC+8"
    assert value["validation_errors"] == []


def test_single_ascii_dash_is_treated_as_placeholder():
    fields = StaticFieldNormalizer().normalize("目的港：PIRAEUS，吃水：-，船旗：-")
    assert fields["fields"] == {"destination": "PIRAEUS"}
    assert set(fields["invalid_fields"]) == {"draft", "flag"}
    # The builder-level placeholder guard also rejects a lone ASCII dash and double dash.
    assert NativeToolRuntime._valid_update_value("-") is False
    assert NativeToolRuntime._valid_update_value("--") is False
    assert NativeToolRuntime._valid_update_value("-- / --") is False
    assert NativeToolRuntime._valid_update_value("12.3") is True
