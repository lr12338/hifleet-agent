import { useEffect, useMemo, useState } from "react";
import { Button, Card, Col, Form, Input, List, Row, Space, Tabs, Typography } from "antd";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";

import { fetchLogDetail, fetchSessionSummaries, fetchSessionTimeline } from "../api/client";
import { JsonViewer } from "../components/common/JsonViewer";
import { StatusTag } from "../components/common/StatusTag";
import { ContextBar } from "../components/page/ContextBar";
import { PageHeader } from "../components/page/PageHeader";
import { EmptyState } from "../components/state/EmptyState";
import { ErrorState } from "../components/state/ErrorState";
import { LoadingState } from "../components/state/LoadingState";
import { useAdminShell } from "../layouts/AdminShell";
import { buildTimeRangeQuery, formatDuration } from "../utils/timeRange";
import type { ApiCallItem, SessionSummaryItem } from "../types";

function extractRequestPreview(call: ApiCallItem): string {
  const requestJson = call.request_json;
  if (!requestJson || typeof requestJson !== "object") return "";
  const messages = (requestJson as Record<string, unknown>).messages;
  if (!Array.isArray(messages)) return "";
  const user = [...messages].reverse().find((item) => typeof item === "object" && item && (item as Record<string, unknown>).role === "user") as
    | Record<string, unknown>
    | undefined;
  if (!user) return "";
  const content = user.content;
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .filter((item) => typeof item === "object" && item && (item as Record<string, unknown>).type === "text")
      .map((item) => String((item as Record<string, unknown>).text || ""))
      .join(" ");
  }
  return "";
}

function extractResponsePreview(call: ApiCallItem): string {
  const responseJson = call.response_json;
  if (!responseJson || typeof responseJson !== "object") return "";
  const messages = (responseJson as Record<string, unknown>).messages;
  if (Array.isArray(messages)) {
    const assistant = [...messages].reverse().find(
      (item) => typeof item === "object" && item && ["ai", "assistant"].includes(String((item as Record<string, unknown>).type || "").toLowerCase())
    ) as Record<string, unknown> | undefined;
    if (assistant && typeof assistant.content === "string") return assistant.content;
  }
  return JSON.stringify(responseJson).slice(0, 160);
}

export function SessionPage() {
  const navigate = useNavigate();
  const { sessionId = "" } = useParams();
  const [searchParams] = useSearchParams();
  const { timeRange, customRange, openDetailDrawer } = useAdminShell();
  const [form] = Form.useForm();
  const [sessions, setSessions] = useState<SessionSummaryItem[]>([]);
  const [selectedSessionId, setSelectedSessionId] = useState(sessionId || searchParams.get("session_id") || "");
  const [calls, setCalls] = useState<ApiCallItem[]>([]);
  const [summary, setSummary] = useState<Record<string, unknown> | null>(null);
  const [loadingList, setLoadingList] = useState(true);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [error, setError] = useState("");

  const loadSessions = async (keyword?: string) => {
    setLoadingList(true);
    setError("");
    try {
      const params = new URLSearchParams({
        page: "1",
        page_size: "20"
      });
      const timeQuery = buildTimeRangeQuery(timeRange, customRange);
      if (timeQuery.start_time) params.set("start_time", timeQuery.start_time);
      if (timeQuery.end_time) params.set("end_time", timeQuery.end_time);
      if (keyword) params.set("keyword", keyword);
      const response = await fetchSessionSummaries(params);
      setSessions(response.items);
      if (!selectedSessionId && response.items[0]?.session_id) {
        setSelectedSessionId(response.items[0].session_id);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载会话列表失败");
    } finally {
      setLoadingList(false);
    }
  };

  const loadSessionDetail = async (nextSessionId: string) => {
    if (!nextSessionId) return;
    setLoadingDetail(true);
    setError("");
    try {
      const response = await fetchSessionTimeline(nextSessionId);
      setCalls(response.calls);
      setSummary(response.summary || null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载会话详情失败");
    } finally {
      setLoadingDetail(false);
    }
  };

  useEffect(() => {
    void loadSessions(form.getFieldValue("keyword"));
  }, [timeRange, customRange]);

  useEffect(() => {
    if (sessionId) {
      setSelectedSessionId(sessionId);
    }
  }, [sessionId]);

  useEffect(() => {
    if (!selectedSessionId) return;
    void loadSessionDetail(selectedSessionId);
  }, [selectedSessionId]);

  const selectedSession = useMemo(
    () => sessions.find((item) => item.session_id === selectedSessionId),
    [selectedSessionId, sessions]
  );

  const openRunDrawer = async (call: ApiCallItem) => {
    const detail = await fetchLogDetail(call.run_id);
    openDetailDrawer({
      title: `运行详情 · ${call.run_id}`,
      subtitle: `${call.route} · ${call.created_at}`,
      width: 720,
      content: <JsonViewer value={detail} maxHeight={520} />
    });
  };

  return (
    <>
      <PageHeader
        title="会话中心"
        description="在一个页面里完成会话发现、消息回放、日志跳转与调试联动。"
        actions={[
          { key: "export", label: "导出", onClick: () => selectedSessionId && navigate(`/logs?session_id=${encodeURIComponent(selectedSessionId)}`) },
          { key: "mark", label: "标记问题", onClick: () => selectedSessionId && navigate(`/logs?session_id=${encodeURIComponent(selectedSessionId)}&status=error`) }
        ]}
      />

      <ContextBar>
        <Form
          form={form}
          layout="inline"
          onFinish={(values) => {
            void loadSessions(values.keyword);
          }}
        >
          <Form.Item name="keyword" label="搜索会话">
            <Input placeholder="session_id / 用户问题关键词" style={{ width: 280 }} />
          </Form.Item>
          <Form.Item>
            <Button type="primary" htmlType="submit">查询</Button>
          </Form.Item>
          <Form.Item>
            <Button onClick={() => { form.resetFields(); void loadSessions(); }}>重置</Button>
          </Form.Item>
        </Form>
        <Typography.Text type="secondary">时间范围来自全局 Header，可从左侧快速切换最近活跃会话。</Typography.Text>
      </ContextBar>

      {error ? <ErrorState title="会话中心加载失败" error={error} onRetry={() => void loadSessions(form.getFieldValue("keyword"))} /> : null}

      <Row gutter={16}>
        <Col xs={24} xl={6}>
          <Card title="会话列表" bordered={false} bodyStyle={{ padding: 0 }}>
            {loadingList ? (
              <LoadingState />
            ) : (
              <List
                dataSource={sessions}
                locale={{ emptyText: <EmptyState title="暂无会话" description="当前时间范围内没有可浏览会话。" /> }}
                renderItem={(item) => (
                  <List.Item
                    style={{
                      cursor: "pointer",
                      padding: 16,
                      background: item.session_id === selectedSessionId ? "#eff6ff" : "#fff",
                      borderLeft: item.session_id === selectedSessionId ? "3px solid #2563eb" : "3px solid transparent"
                    }}
                    onClick={() => {
                      setSelectedSessionId(item.session_id);
                      navigate(`/sessions/${encodeURIComponent(item.session_id)}`);
                    }}
                  >
                    <List.Item.Meta
                      title={item.title || item.session_id}
                      description={
                        <Space direction="vertical" size={4}>
                          <Typography.Text type="secondary">{item.last_message || item.session_id}</Typography.Text>
                          <Space size={8} wrap>
                            <StatusTag status={item.latest_status} />
                            <Typography.Text type="secondary">轮次 {item.turn_count}</Typography.Text>
                            <Typography.Text type="secondary">工具 {item.tool_count}</Typography.Text>
                          </Space>
                        </Space>
                      }
                    />
                  </List.Item>
                )}
              />
            )}
          </Card>
        </Col>

        <Col xs={24} xl={11}>
          <Card
            title={selectedSession?.title || selectedSessionId || "消息流"}
            bordered={false}
            extra={
              selectedSessionId ? (
                <Space>
                  <Button type="link" onClick={() => navigate(`/logs?session_id=${encodeURIComponent(selectedSessionId)}`)}>查看日志</Button>
                  <Button type="link" onClick={() => navigate(`/chat?session_id=${encodeURIComponent(selectedSessionId)}`)}>打开调试</Button>
                </Space>
              ) : null
            }
          >
            {loadingDetail ? (
              <LoadingState />
            ) : !selectedSessionId ? (
              <EmptyState title="请选择左侧会话" description="选择一个会话后，可查看对应消息流、关联日志和元信息。" />
            ) : (
              <Space direction="vertical" style={{ width: "100%" }} size={16}>
                {calls.map((call) => (
                  <Card key={call.run_id} size="small" style={{ borderRadius: 12 }}>
                    <Space direction="vertical" size={10} style={{ width: "100%" }}>
                      <Space style={{ width: "100%", justifyContent: "space-between" }}>
                        <Space>
                          <StatusTag status={call.status} />
                          <Typography.Text strong>{call.route}</Typography.Text>
                          <Typography.Text type="secondary">{call.created_at}</Typography.Text>
                        </Space>
                        <Space>
                          <Typography.Text type="secondary">{formatDuration(call.latency_ms)}</Typography.Text>
                          <Button type="link" onClick={() => void openRunDrawer(call)}>查看 Trace</Button>
                        </Space>
                      </Space>
                      <Tabs
                        size="small"
                        items={[
                          {
                            key: "user",
                            label: "用户输入",
                            children: <Typography.Paragraph style={{ whiteSpace: "pre-wrap", marginBottom: 0 }}>{extractRequestPreview(call) || "-"}</Typography.Paragraph>
                          },
                          {
                            key: "assistant",
                            label: "助手响应",
                            children: <Typography.Paragraph style={{ whiteSpace: "pre-wrap", marginBottom: 0 }}>{extractResponsePreview(call) || "-"}</Typography.Paragraph>
                          },
                          {
                            key: "raw",
                            label: "原始记录",
                            children: <JsonViewer value={{ request: call.request_json, response: call.response_json }} maxHeight={280} />
                          }
                        ]}
                      />
                    </Space>
                  </Card>
                ))}
              </Space>
            )}
          </Card>
        </Col>

        <Col xs={24} xl={7}>
          <Space direction="vertical" style={{ width: "100%" }} size={16}>
            <Card title="会话详情" bordered={false}>
              {selectedSession ? (
                <Space direction="vertical" size={12} style={{ width: "100%" }}>
                  <div><Typography.Text type="secondary">session_id</Typography.Text><div>{selectedSession.session_id}</div></div>
                  <div><Typography.Text type="secondary">用户 / 渠道</Typography.Text><div>{selectedSession.user_id || "-"} / {selectedSession.source_channel || "-"}</div></div>
                  <div><Typography.Text type="secondary">最近状态</Typography.Text><div><StatusTag status={selectedSession.latest_status} /></div></div>
                  <div><Typography.Text type="secondary">轮次 / 工具 / 错误</Typography.Text><div>{selectedSession.turn_count} / {selectedSession.tool_count} / {selectedSession.error_count}</div></div>
                  <div><Typography.Text type="secondary">平均延迟</Typography.Text><div>{selectedSession.avg_latency_ms}ms</div></div>
                </Space>
              ) : (
                <EmptyState title="暂无会话详情" />
              )}
            </Card>

            <Card title="执行轨迹" bordered={false}>
              <JsonViewer value={summary || {}} maxHeight={260} />
            </Card>

            <Card title="联动操作" bordered={false}>
              <Space direction="vertical" style={{ width: "100%" }}>
                <Button block onClick={() => selectedSessionId && navigate(`/logs?session_id=${encodeURIComponent(selectedSessionId)}`)}>跳转请求日志</Button>
                <Button block onClick={() => selectedSessionId && navigate(`/chat?session_id=${encodeURIComponent(selectedSessionId)}`)}>跳转 Chat Debug</Button>
                <Button block onClick={() => selectedSession?.latest_run_id && navigate(`/logs/${encodeURIComponent(selectedSession.latest_run_id)}`)}>打开最近调用详情</Button>
              </Space>
            </Card>
          </Space>
        </Col>
      </Row>
    </>
  );
}
