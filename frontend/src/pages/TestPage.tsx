import { useMemo, useState } from "react";
import { Button, Card, Col, Form, Input, List, Row, Select, Space, Typography, message } from "antd";

import { runTest, streamTestRun, type StreamRunEvent } from "../api/client";
import { JsonViewer } from "../components/common/JsonViewer";
import { ContextBar } from "../components/page/ContextBar";
import { PageHeader } from "../components/page/PageHeader";
import { StatusTag } from "../components/common/StatusTag";

const defaultPayload = `{
  "messages": [{"role":"user","content":"你好"}],
  "session_id": "admin_test_s1",
  "user_id": "admin",
  "source_channel": "admin_panel",
  "agent_profile": "employee_assistant"
}`;

const REQUEST_HISTORY_KEY = "agent-admin-test-history";

function buildAdminRunId() {
  return `admin-test-${Date.now()}`;
}

export function TestPage() {
  const [form] = Form.useForm();
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [streamEvents, setStreamEvents] = useState<StreamRunEvent[]>([]);
  const [loading, setLoading] = useState(false);
  const [responseMeta, setResponseMeta] = useState<{
    status?: number;
    latencyMs?: number;
    runId?: string;
    sessionId?: string;
    userId?: string;
    sourceChannel?: string;
    agentProfile?: string;
  }>({});
  const recentRequests = useMemo(() => {
    const raw = window.localStorage.getItem(REQUEST_HISTORY_KEY);
    return raw ? (JSON.parse(raw) as Array<{ endpoint: string; payload: string }>) : [];
  }, [loading]);

  return (
    <>
      <PageHeader
        title="API Playground"
        description="构造 `/run` 与 `/stream_run` 请求，查看状态码、流式事件、Trace 与原始请求响应。"
        actions={[
          {
            key: "history",
            label: "历史模板",
            onClick: () => {
              if (!recentRequests.length) {
                message.info("暂无最近请求历史");
              }
            }
          },
          {
            key: "save",
            label: "保存当前请求",
            onClick: () => {
              const values = form.getFieldsValue();
              const next = [{ endpoint: values.endpoint, payload: values.payload }, ...recentRequests].slice(0, 8);
              window.localStorage.setItem(REQUEST_HISTORY_KEY, JSON.stringify(next));
              message.success("已保存到最近请求历史");
            }
          }
        ]}
      />

      <ContextBar>
        <Typography.Text type="secondary">支持同步 / 流式接口切换，底部展示最近请求与示例 payload，便于复现线上问题。</Typography.Text>
      </ContextBar>

      <Row gutter={16}>
        <Col xs={24} xl={11}>
          <Card title="请求构造" bordered={false}>
            <Form
              form={form}
              layout="vertical"
              initialValues={{ endpoint: "/run", method: "POST", headers: "{\n  \"x-admin-api-key\": \"***\"\n}", payload: defaultPayload }}
              onFinish={async (vals) => {
                setLoading(true);
                setResult(null);
                setStreamEvents([]);
                const startedAt = Date.now();
                try {
                  const parsedPayload = JSON.parse(vals.payload || "{}");
                  const runId = buildAdminRunId();
                  ["user_id", "session_id", "source_channel", "agent_profile", "model", "temperature", "tool_policy"].forEach((key) => {
                    if (vals[key]) parsedPayload[key] = vals[key];
                  });
                  window.localStorage.setItem(
                    REQUEST_HISTORY_KEY,
                    JSON.stringify([{ endpoint: vals.endpoint, payload: vals.payload }, ...recentRequests].slice(0, 8))
                  );
                  if (vals.endpoint === "/stream_run") {
                    const collectedEvents: StreamRunEvent[] = [];
                    await streamTestRun(parsedPayload, {
                      onEvent: (event) => {
                        collectedEvents.push(event);
                        setStreamEvents((prev) => [...prev, event]);
                      },
                      onDone: () => {
                        setResponseMeta({
                          status: 200,
                          latencyMs: Date.now() - startedAt,
                          runId,
                          sessionId: String(parsedPayload.session_id || "-"),
                          userId: String(parsedPayload.user_id || "-"),
                          sourceChannel: String(parsedPayload.source_channel || "-"),
                          agentProfile: String(parsedPayload.agent_profile || "-")
                        });
                      }
                    }, undefined, runId);
                    setResult({
                      message: "stream completed",
                      event_count: collectedEvents.length,
                      run_id: runId,
                      session_id: parsedPayload.session_id || "-",
                      user_id: parsedPayload.user_id || "-",
                      source_channel: parsedPayload.source_channel || "-",
                      agent_profile: parsedPayload.agent_profile || "-"
                    });
                  } else {
                    const res = await runTest({
                      endpoint: vals.endpoint,
                      payload: parsedPayload,
                      run_id: runId,
                      stream: false
                    });
                    setResult(res);
                    const body = (res.body || {}) as Record<string, unknown>;
                    setResponseMeta({
                      status: Number(res.status_code || 200),
                      latencyMs: Date.now() - startedAt,
                      runId: String(body.run_id || runId),
                      sessionId: String(parsedPayload.session_id || "-"),
                      userId: String(parsedPayload.user_id || "-"),
                      sourceChannel: String(parsedPayload.source_channel || "-"),
                      agentProfile: String(parsedPayload.agent_profile || "-")
                    });
                  }
                } catch (error) {
                  message.error(error instanceof Error ? error.message : "请求失败");
                } finally {
                  setLoading(false);
                }
              }}
            >
              <Row gutter={12}>
                <Col span={12}>
                  <Form.Item label="Endpoint" name="endpoint">
                    <Select
                      options={[
                        { label: "/run", value: "/run" },
                        { label: "/stream_run", value: "/stream_run" }
                      ]}
                    />
                  </Form.Item>
                </Col>
                <Col span={12}>
                  <Form.Item label="Method" name="method">
                    <Select options={[{ label: "POST", value: "POST" }]} />
                  </Form.Item>
                </Col>
              </Row>
              <Form.Item label="Headers(JSON)" name="headers">
                <Input.TextArea rows={4} />
              </Form.Item>
              <Form.Item label="Payload(JSON / Form)" name="payload">
                <Input.TextArea rows={16} />
              </Form.Item>
              <Row gutter={12}>
                <Col span={8}><Form.Item label="user_id" name="user_id"><Input placeholder="可选覆盖" /></Form.Item></Col>
                <Col span={8}><Form.Item label="session_id" name="session_id"><Input placeholder="可选覆盖" /></Form.Item></Col>
                <Col span={8}><Form.Item label="source_channel" name="source_channel"><Input placeholder="admin_panel" /></Form.Item></Col>
                <Col span={8}><Form.Item label="agent_profile" name="agent_profile"><Select allowClear options={[{ value: "customer_support", label: "customer_support" }, { value: "employee_assistant", label: "employee_assistant" }]} /></Form.Item></Col>
              </Row>
              <Card size="small" title="高级参数" style={{ marginBottom: 16 }}>
                <Row gutter={12}>
                  <Col span={8}><Form.Item label="模型" name="model"><Input placeholder="可选" /></Form.Item></Col>
                  <Col span={8}><Form.Item label="温度" name="temperature"><Input placeholder="0.7" /></Form.Item></Col>
                  <Col span={8}><Form.Item label="工具策略" name="tool_policy"><Input placeholder="auto" /></Form.Item></Col>
                </Row>
              </Card>
              <Space>
                <Button type="primary" htmlType="submit" loading={loading}>发送</Button>
                <Button onClick={() => form.setFieldsValue({ payload: defaultPayload })}>恢复示例</Button>
              </Space>
            </Form>
          </Card>
        </Col>

        <Col xs={24} xl={13}>
          <Space direction="vertical" style={{ width: "100%" }} size={16}>
            <Card title="响应状态" bordered={false}>
              <Space size={24}>
                <div><Typography.Text type="secondary">状态码</Typography.Text><div><StatusTag status={String(responseMeta.status || "-")} /></div></div>
                <div><Typography.Text type="secondary">耗时</Typography.Text><div>{responseMeta.latencyMs ? `${responseMeta.latencyMs} ms` : "-"}</div></div>
                <div><Typography.Text type="secondary">run_id</Typography.Text><div>{responseMeta.runId || "-"}</div></div>
              </Space>
              <Space size={24} wrap style={{ marginTop: 12 }}>
                <div><Typography.Text type="secondary">session_id</Typography.Text><div>{responseMeta.sessionId || "-"}</div></div>
                <div><Typography.Text type="secondary">user_id</Typography.Text><div>{responseMeta.userId || "-"}</div></div>
                <div><Typography.Text type="secondary">source_channel</Typography.Text><div>{responseMeta.sourceChannel || "-"}</div></div>
                <div><Typography.Text type="secondary">agent_profile</Typography.Text><div>{responseMeta.agentProfile || "-"}</div></div>
              </Space>
            </Card>

            <Card title="Response Body" bordered={false}>
              <JsonViewer value={result || { message: "暂无同步响应" }} maxHeight={260} />
            </Card>

            <Card title="Streaming 输出" bordered={false}>
              <JsonViewer value={streamEvents.map((item) => ({ event: item.event, data: item.data }))} maxHeight={260} />
            </Card>

            <Card title="最近请求 / 示例 Payload" bordered={false}>
              <List
                dataSource={recentRequests}
                locale={{ emptyText: "暂无最近请求历史" }}
                renderItem={(item) => (
                  <List.Item
                    actions={[
                      <Button key="apply" type="link" onClick={() => form.setFieldsValue({ endpoint: item.endpoint, payload: item.payload })}>
                        应用
                      </Button>
                    ]}
                  >
                    <List.Item.Meta title={item.endpoint} description={<Typography.Text type="secondary">{item.payload.slice(0, 120)}</Typography.Text>} />
                  </List.Item>
                )}
              />
            </Card>

            <Card title="开发者模式 Raw Request / Raw Response" bordered={false}>
              <Row gutter={12}>
                <Col span={12}><JsonViewer value={form.getFieldsValue()} maxHeight={220} /></Col>
                <Col span={12}><JsonViewer value={{ result, streamEvents, responseMeta }} maxHeight={220} /></Col>
              </Row>
            </Card>
          </Space>
        </Col>
      </Row>
    </>
  );
}
