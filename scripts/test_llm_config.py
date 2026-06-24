import json
import os
import tempfile
from pathlib import Path

from admin_api.schemas import LLMConfigRequest
from admin_api.service import get_llm_config, update_llm_config
from llm_config import build_thinking_payload, load_llm_config, messages_have_multimodal_content, resolve_model_selection, save_llm_config


def assert_equal(actual, expected, message):
    if actual != expected:
        raise AssertionError(f"{message}: expected={expected!r}, actual={actual!r}")


def main():
    original_workspace = os.environ.get('COZE_WORKSPACE_PATH')
    with tempfile.TemporaryDirectory(prefix='hifleet_llm_cfg_') as tmp_dir:
        workspace = Path(tmp_dir)
        os.environ['COZE_WORKSPACE_PATH'] = str(workspace)
        save_llm_config(
            {
                'config': {
                    'model': 'legacy-model',
                    'temperature': 0.3,
                    'thinking_type': 'disabled',
                    'reasoning_effort': 'high',
                },
                'tools': ['inspect_tabular_file'],
            },
            str(workspace),
        )

        loaded = load_llm_config(str(workspace))
        assert_equal(loaded['config']['text_model'], 'legacy-model', 'legacy model should migrate to text_model')
        assert_equal(loaded['config']['multimodal_model'], 'doubao-seed-2-0-lite-260428', 'default multimodal model should be injected')
        assert_equal(loaded['config']['reasoning_effort'], 'minimal', 'disabled thinking should force minimal effort')

        text_route = resolve_model_selection(loaded, has_multimodal_input=False)
        multimodal_route = resolve_model_selection(loaded, has_multimodal_input=True)
        assert_equal(text_route['model'], 'legacy-model', 'text route should use text model')
        assert_equal(multimodal_route['model'], 'doubao-seed-2-0-lite-260428', 'multimodal route should use multimodal model')
        assert_equal(
            load_llm_config(str(workspace / 'missing'))['config']['text_model'],
            'doubao-seed-2-0-lite-260428',
            'default text model should be seed lite',
        )

        assert_equal(
            messages_have_multimodal_content([
                {'role': 'user', 'content': 'hello'}
            ]),
            False,
            'plain text should not be detected as multimodal',
        )
        assert_equal(
            messages_have_multimodal_content([
                {'role': 'user', 'content': [{'type': 'image_url', 'image_url': {'url': 'https://example.com/a.png'}}, {'type': 'text', 'text': 'describe'}]}
            ]),
            True,
            'image input should be detected as multimodal',
        )

        updated = update_llm_config(
            LLMConfigRequest(
                text_model='doubao-seed-2-0-lite-260428',
                multimodal_model='doubao-seed-2-0-lite-260428',
                thinking_type='enabled',
                reasoning_effort='high',
            )
        )
        assert_equal(updated['text_model'], 'doubao-seed-2-0-lite-260428', 'admin update should persist text model')
        assert_equal(updated['thinking_type'], 'enabled', 'admin update should persist enabled thinking')
        assert_equal(updated['reasoning_effort'], 'high', 'enabled thinking should persist requested effort')
        assert_equal(updated['deep_thinking_enabled'], True, 'enabled thinking should set deep_thinking_enabled')

        view = get_llm_config()
        assert_equal(view['thinking_type'], 'enabled', 'get_llm_config should reflect normalized thinking type')
        persisted = json.loads((workspace / 'config' / 'agent_llm_config.json').read_text(encoding='utf-8'))
        assert_equal(persisted['config']['model'], 'doubao-seed-2-0-lite-260428', 'legacy model field should follow text model')
        assert_equal(
            build_thinking_payload('disabled', 'high'),
            {'type': 'disabled', 'reasoning_effort': 'minimal'},
            'disabled thinking payload must force minimal effort',
        )
        assert_equal(
            build_thinking_payload('auto', ''),
            {'type': 'enabled', 'reasoning_effort': 'medium'},
            'auto should be normalized to seed-lite-safe medium thinking',
        )
        legacy_auto_route = resolve_model_selection(
            persisted,
            has_multimodal_input=False,
            requested_thinking='auto',
        )
        assert_equal(legacy_auto_route['thinking_type'], 'enabled', 'legacy request auto should normalize to enabled')
        assert_equal(legacy_auto_route['reasoning_effort'], 'high', 'legacy request auto should keep configured effort')

    if original_workspace is not None:
        os.environ['COZE_WORKSPACE_PATH'] = original_workspace
    else:
        os.environ.pop('COZE_WORKSPACE_PATH', None)
    print('test_llm_config: ok')


if __name__ == '__main__':
    main()
