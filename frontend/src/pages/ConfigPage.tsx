import { useEffect, useState } from 'react';
import { Alert, AutoComplete, Button, Card, Col, Form, Row, Space, Switch, Tag, Typography, message } from 'antd';

import { fetchLlmConfig, saveLlmConfig, type LlmRuntimeConfigResponse } from '../api/client';
import { ContextBar } from '../components/page/ContextBar';
import { PageHeader } from '../components/page/PageHeader';
import './ConfigPage.css';

interface ConfigFormValues {
  text_model: string;
  multimodal_model: string;
  deep_thinking_enabled: boolean;
}

function toOptions(values: string[]) {
  return values.map((value) => ({ value, label: value }));
}

export function ConfigPage() {
  const [form] = Form.useForm<ConfigFormValues>();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [config, setConfig] = useState<LlmRuntimeConfigResponse | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      setLoading(true);
      try {
        const data = await fetchLlmConfig();
        if (cancelled) return;
        setConfig(data);
        form.setFieldsValue({
          text_model: data.text_model,
          multimodal_model: data.multimodal_model,
          deep_thinking_enabled: data.deep_thinking_enabled
        });
      } catch (error) {
        const msg = error instanceof Error ? error.message : '加载配置失败';
        if (msg !== 'UNAUTHORIZED') {
          message.error('模型配置加载失败');
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [form]);

  const handleSubmit = async (values: ConfigFormValues) => {
    setSaving(true);
    try {
      const payload = {
        text_model: values.text_model.trim(),
        multimodal_model: values.multimodal_model.trim(),
        thinking_type: values.deep_thinking_enabled ? 'enabled' : 'disabled'
      } as const;
      const data = await saveLlmConfig(payload);
      setConfig(data);
      form.setFieldsValue({
        text_model: data.text_model,
        multimodal_model: data.multimodal_model,
        deep_thinking_enabled: data.deep_thinking_enabled
      });
      message.success('模型配置已更新');
    } catch (error) {
      const msg = error instanceof Error ? error.message : '保存失败';
      if (msg !== 'UNAUTHORIZED') {
        message.error('模型配置保存失败');
      }
    } finally {
      setSaving(false);
    }
  };

  const resetForm = () => {
    if (!config) return;
    form.setFieldsValue({
      text_model: config.text_model,
      multimodal_model: config.multimodal_model,
      deep_thinking_enabled: config.deep_thinking_enabled
    });
  };

  return (
    <div className="config-page">
      <PageHeader
        title="模型配置中心"
        description="统一管理文本与多模态模型的默认路由策略，配置会直接影响 /run、/stream_run 与管理台调试调用。"
      />
      <ContextBar>
        <Space size={[8, 8]} wrap>
          <Tag color="blue">纯文本 -&gt; 文本模型</Tag>
          <Tag color="gold">图片 / 音频 / 视频 -&gt; 多模态模型</Tag>
          <Tag color={config?.deep_thinking_enabled ? 'green' : 'default'}>
            深度思考：{config?.deep_thinking_enabled ? '开启' : '关闭'}
          </Tag>
        </Space>
      </ContextBar>

      <Row gutter={[16, 16]}>
        <Col xs={24} xl={16}>
          <Card bordered={false} loading={loading} className="config-page-card">
            <div className="config-page-card-head">
              <div>
                <Typography.Title level={5}>默认模型路由</Typography.Title>
                <Typography.Paragraph type="secondary">
                  调用接口时，系统会自动识别输入是否包含图片、语音或视频，并选择对应的默认模型。这里支持直接使用预置模型，也支持填写自定义模型 ID。
                </Typography.Paragraph>
              </div>
              <Tag color="processing">Ark Responses API</Tag>
            </div>

            <Form form={form} layout="vertical" onFinish={handleSubmit}>
              <Row gutter={[16, 0]}>
                <Col xs={24} md={12}>
                  <Form.Item
                    label="文本模型"
                    name="text_model"
                    rules={[{ required: true, message: '请输入文本模型 ID' }]}
                    extra="默认纯文本问答、分析、总结、工具调度均使用此模型。"
                  >
                    <AutoComplete
                      options={toOptions(config?.text_model_presets || [])}
                      placeholder="例如 doubao-seed-2-0-pro-260215"
                      filterOption={(input, option) => String(option?.value || '').toLowerCase().includes(input.toLowerCase())}
                    />
                  </Form.Item>
                </Col>
                <Col xs={24} md={12}>
                  <Form.Item
                    label="多模态模型"
                    name="multimodal_model"
                    rules={[{ required: true, message: '请输入多模态模型 ID' }]}
                    extra="默认图片、语音、视频理解任务使用此模型。"
                  >
                    <AutoComplete
                      options={toOptions(config?.multimodal_model_presets || [])}
                      placeholder="例如 doubao-seed-2-0-lite-260428"
                      filterOption={(input, option) => String(option?.value || '').toLowerCase().includes(input.toLowerCase())}
                    />
                  </Form.Item>
                </Col>
              </Row>

              <div className="config-page-thinking-row">
                <Form.Item label="深度思考模式" name="deep_thinking_enabled" valuePropName="checked" style={{ marginBottom: 0 }}>
                  <Switch checkedChildren="开启" unCheckedChildren="关闭" />
                </Form.Item>
                <Typography.Text type="secondary">
                  开启后默认向模型下发 `thinking.type=enabled`；关闭时使用 `disabled`。
                </Typography.Text>
              </div>

              <div className="admin-form-actions config-page-actions">
                <Button onClick={resetForm}>重置</Button>
                <Button type="primary" htmlType="submit" loading={saving}>
                  保存并生效
                </Button>
              </div>
            </Form>
          </Card>
        </Col>

        <Col xs={24} xl={8}>
          <Card bordered={false} loading={loading} className="config-page-card config-page-summary-card">
            <Typography.Title level={5}>生效预览</Typography.Title>
            <div className="config-page-summary-grid">
              <div className="config-page-summary-item">
                <span className="config-page-summary-label">文本请求</span>
                <strong>{config?.text_model || '-'}</strong>
              </div>
              <div className="config-page-summary-item">
                <span className="config-page-summary-label">多模态请求</span>
                <strong>{config?.multimodal_model || '-'}</strong>
              </div>
              <div className="config-page-summary-item">
                <span className="config-page-summary-label">思考策略</span>
                <strong>{config?.thinking_type || '-'}</strong>
              </div>
            </div>
            <Alert
              showIcon
              type="info"
              className="config-page-alert"
              message="接口路由规则"
              description="系统会在请求进入 /run、/stream_run 和后台调试转发前识别消息内容：纯文本使用文本模型，图片、音频、视频输入自动切换到多模态模型。"
            />
          </Card>
        </Col>
      </Row>
    </div>
  );
}
