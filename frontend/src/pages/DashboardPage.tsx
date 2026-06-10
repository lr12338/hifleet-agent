import { useEffect, useMemo, useState } from "react";
import { Button, Card, Col, List, Progress, Row, Space, Statistic, Tag, Typography } from "antd";
import { useNavigate } from "react-router-dom";

import { fetchDashboardSummary } from "../api/client";
import { ContextBar } from "../components/page/ContextBar";
import { PageHeader } from "../components/page/PageHeader";
import { EmptyState } from "../components/state/EmptyState";
import { ErrorState } from "../components/state/ErrorState";
import { LoadingState } from "../components/state/LoadingState";
import { StatusTag } from "../components/common/StatusTag";
import { useAdminShell } from "../layouts/AdminShell";
import { buildTimeRangeQuery, formatDuration } from "../utils/timeRange";
import type { DashboardSummary } from "../types";

function MiniTrend({ data, color }: { data: number[]; color: string }) {
  const max = Math.max(...data, 1);
  return (
    <div style={{ display: "flex", alignItems: "flex-end", gap: 6, height: 120 }}>
      {data.map((value, index) => (
        <div
          key={`${index}-${value}`}
          style={{
            flex: 1,
            borderRadius: 8,
            background: color,
            minHeight: 8,
            height: `${Math.max(8, (value / max) * 100)}%`
          }}
        />
      ))}
    </div>
  );
}

export function DashboardPage() {
  const navigate = useNavigate();
  const { timeRange, customRange, environmentLabel } = useAdminShell();
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const params = new URLSearchParams(buildTimeRangeQuery(timeRange, customRange) as Record<string, string>);
      const response = await fetchDashboardSummary(params);
      setSummary(response);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载总览失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, [timeRange, customRange]);

  const trendData = useMemo(() => summary?.trends || [], [summary]);

  return (
    <>
      <PageHeader
        title="运营总览"
        description="从调用、会话、错误、工具与依赖状态五个维度观察 Agent 后台运行情况。"
        extra={<Typography.Text type="secondary">环境：{environmentLabel} · 时间范围由全局 Header 控制</Typography.Text>}
        actions={[
          { key: "export", label: "导出", onClick: () => window.open("/admin/logs?page=1&page_size=200", "_blank") },
          { key: "alert", label: "告警", onClick: () => navigate("/config") },
          { key: "refresh", label: "刷新", onClick: () => void load(), type: "primary" }
        ]}
      />

      <ContextBar>
        <Tag color="blue">Agent Profile 已接入日志观测</Tag>
        <Tag color="gold">日志 / 会话 / 调试 / API 已可联动</Tag>
        <Typography.Text type="secondary">点击下方 KPI、异常会话与分布榜单可进一步钻取排障。</Typography.Text>
      </ContextBar>

      {loading ? <LoadingState /> : null}
      {!loading && error ? <ErrorState title="运营总览加载失败" error={error} onRetry={() => void load()} /> : null}
      {!loading && !error && !summary ? <EmptyState title="暂无总览数据" description="请先产生 /run 或 /stream_run 调用。" /> : null}

      {!loading && !error && summary ? (
        <Space direction="vertical" size={16} style={{ width: "100%" }}>
          <Row gutter={[16, 16]}>
            <Col xs={24} md={8} xl={4}>
              <Card bordered={false}><Statistic title="调用量" value={summary.kpis.request_count} /></Card>
            </Col>
            <Col xs={24} md={8} xl={4}>
              <Card bordered={false}><Statistic title="会话数" value={summary.kpis.session_count} /></Card>
            </Col>
            <Col xs={24} md={8} xl={4}>
              <Card bordered={false}><Statistic title="成功率" suffix="%" value={summary.kpis.success_rate} precision={2} /></Card>
            </Col>
            <Col xs={24} md={8} xl={4}>
              <Card bordered={false}><Statistic title="平均延迟" value={summary.kpis.avg_latency_ms} suffix="ms" /></Card>
            </Col>
            <Col xs={24} md={8} xl={4}>
              <Card bordered={false}><Statistic title="工具成功率" suffix="%" value={summary.kpis.tool_success_rate} precision={2} /></Card>
            </Col>
            <Col xs={24} md={8} xl={4}>
              <Card bordered={false}><Statistic title="估算成本" prefix="¥" value={summary.kpis.estimated_cost} precision={2} /></Card>
            </Col>
          </Row>

          <Row gutter={[16, 16]}>
            <Col xs={24} xl={16}>
              <Card
                bordered={false}
                title="调用趋势"
                extra={<Button type="link" onClick={() => navigate("/logs")}>查看日志</Button>}
              >
                {trendData.length ? (
                  <>
                    <MiniTrend data={trendData.map((item) => item.requests)} color="#2563eb" />
                    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))", gap: 12, marginTop: 12 }}>
                      {trendData.slice(-6).map((item) => (
                        <Card key={item.bucket} size="small">
                          <Typography.Text type="secondary">{item.bucket}</Typography.Text>
                          <div>请求 {item.requests}</div>
                          <div>错误 {item.errors}</div>
                          <div>延迟 {item.avg_latency_ms}ms</div>
                        </Card>
                      ))}
                    </div>
                  </>
                ) : (
                  <EmptyState title="暂无趋势数据" />
                )}
              </Card>
            </Col>
            <Col xs={24} xl={8}>
              <Card bordered={false} title="错误率与延迟">
                <Space direction="vertical" style={{ width: "100%" }} size={16}>
                  <div>
                    <Typography.Text type="secondary">错误占比</Typography.Text>
                    <Progress percent={summary.kpis.request_count ? Number(((summary.kpis.error_count / summary.kpis.request_count) * 100).toFixed(2)) : 0} status="exception" />
                  </div>
                  <div>
                    <Typography.Text type="secondary">平均延迟</Typography.Text>
                    <Progress percent={Math.min(100, Math.round(summary.kpis.avg_latency_ms / 20))} />
                    <Typography.Text type="secondary">{formatDuration(summary.kpis.avg_latency_ms)}</Typography.Text>
                  </div>
                  <div>
                    <Typography.Text type="secondary">工具成功率</Typography.Text>
                    <Progress percent={summary.kpis.tool_success_rate} status="active" />
                  </div>
                </Space>
              </Card>
            </Col>
          </Row>

          <Row gutter={[16, 16]}>
            <Col xs={24} xl={6}>
              <Card bordered={false} title="热门渠道">
                <List
                  dataSource={summary.distribution.by_channel}
                  renderItem={(item) => (
                    <List.Item actions={[<Button type="link" onClick={() => navigate(`/logs?source_channel=${encodeURIComponent(item.label)}`)}>跳转</Button>]}>
                      <List.Item.Meta title={item.label} description={`${item.value} 次请求`} />
                    </List.Item>
                  )}
                />
              </Card>
            </Col>
            <Col xs={24} xl={6}>
              <Card bordered={false} title="热门路由">
                <List
                  dataSource={summary.distribution.by_route}
                  renderItem={(item) => (
                    <List.Item actions={[<Button type="link" onClick={() => navigate(`/logs?route=${encodeURIComponent(item.label)}`)}>查看</Button>]}>
                      <List.Item.Meta title={item.label} description={`${item.value} 次调用`} />
                    </List.Item>
                  )}
                />
              </Card>
            </Col>
            <Col xs={24} xl={6}>
              <Card bordered={false} title="Agent Profile">
                <List
                  dataSource={summary.distribution.by_profile || []}
                  renderItem={(item) => (
                    <List.Item actions={[<Button type="link" onClick={() => navigate(`/logs?agent_profile=${encodeURIComponent(item.label)}`)}>查看</Button>]}>
                      <List.Item.Meta title={item.label} description={`${item.value} 次调用`} />
                    </List.Item>
                  )}
                />
              </Card>
            </Col>
            <Col xs={24} xl={6}>
              <Card bordered={false} title="系统健康">
                <List
                  dataSource={[
                    { label: "服务状态", value: summary.health.service },
                    { label: "模型状态", value: summary.health.model },
                    { label: "依赖状态", value: summary.health.dependencies },
                    { label: "最近版本", value: summary.health.version }
                  ]}
                  renderItem={(item) => (
                    <List.Item>
                      <Space style={{ width: "100%", justifyContent: "space-between" }}>
                        <Typography.Text>{item.label}</Typography.Text>
                        <StatusTag status={item.value} />
                      </Space>
                    </List.Item>
                  )}
                />
              </Card>
            </Col>
          </Row>

          <Card
            bordered={false}
            title="高风险会话"
            extra={<Button type="link" onClick={() => navigate("/sessions")}>查看会话中心</Button>}
          >
            <List
              dataSource={summary.risky_sessions}
              locale={{ emptyText: "暂无高风险会话" }}
              renderItem={(item) => (
                <List.Item
                  actions={[
                    <Button key="session" type="link" onClick={() => navigate(`/sessions/${encodeURIComponent(item.session_id)}`)}>
                      打开会话
                    </Button>,
                    <Button key="logs" type="link" onClick={() => navigate(`/logs?session_id=${encodeURIComponent(item.session_id)}`)}>
                      查看日志
                    </Button>
                  ]}
                >
                  <List.Item.Meta
                    title={item.session_id}
                    description={`用户 ${item.user_id || "-"} · 渠道 ${item.source_channel || "-"} · Profile ${item.agent_profile || "-"} · 最近活跃 ${item.updated_at}`}
                  />
                  <Space size={24}>
                    <Typography.Text>轮次 {item.turn_count}</Typography.Text>
                    <Typography.Text type="danger">异常 {item.error_count}</Typography.Text>
                    <Typography.Text>平均延迟 {item.avg_latency_ms}ms</Typography.Text>
                  </Space>
                </List.Item>
              )}
            />
          </Card>
        </Space>
      ) : null}
    </>
  );
}
