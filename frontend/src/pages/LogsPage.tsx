import { useEffect, useMemo, useState } from "react";
import { Button, Card, Col, Form, Input, Row, Select, Space, Table, Tabs, Typography, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useNavigate, useSearchParams } from "react-router-dom";

import { fetchLogDetail, fetchLogs } from "../api/client";
import { JsonViewer, TruncatedText } from "../components/common/JsonViewer";
import { StatusTag } from "../components/common/StatusTag";
import { ContextBar } from "../components/page/ContextBar";
import { PageHeader } from "../components/page/PageHeader";
import { EmptyState } from "../components/state/EmptyState";
import { ErrorState } from "../components/state/ErrorState";
import { useAdminShell } from "../layouts/AdminShell";
import { buildTimeRangeQuery, formatDuration } from "../utils/timeRange";
import type { ApiCallItem } from "../types";

const SAVED_LOG_FILTER_KEY = "agent-admin-logs-filters";

export function LogsPage() {
  const [form] = Form.useForm();
  const [items, setItems] = useState<ApiCallItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [page, setPage] = useState(1);
  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>([]);
  const [stats, setStats] = useState({
    request_count: 0,
    failure_count: 0,
    timeout_count: 0,
    avg_latency_ms: 0,
    stream_ratio: 0
  });
  const [filters, setFilters] = useState<{
    route?: string;
    session_id?: string;
    user_id?: string;
    source_channel?: string;
    agent_profile?: string;
    status?: string;
    keyword?: string;
  }>({});
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const { timeRange, customRange, openDetailDrawer, environmentLabel } = useAdminShell();

  const initialFilters = useMemo(
    () => ({
      route: searchParams.get("route") || undefined,
      session_id: searchParams.get("session_id") || undefined,
      user_id: searchParams.get("user_id") || undefined,
      source_channel: searchParams.get("source_channel") || undefined,
      agent_profile: searchParams.get("agent_profile") || undefined,
      status: searchParams.get("status") || undefined,
      keyword: searchParams.get("keyword") || undefined
    }),
    [searchParams]
  );

  const load = async (nextPage = page, nextFilters = filters) => {
    setLoading(true);
    setError("");
    const params = new URLSearchParams({
      page: String(nextPage),
      page_size: "20"
    });
    const timeQuery = buildTimeRangeQuery(timeRange, customRange);
    if (timeQuery.start_time) params.set("start_time", timeQuery.start_time);
    if (timeQuery.end_time) params.set("end_time", timeQuery.end_time);
    if (nextFilters.route) params.set("route", nextFilters.route);
    if (nextFilters.session_id) params.set("session_id", nextFilters.session_id);
    if (nextFilters.user_id) params.set("user_id", nextFilters.user_id);
    if (nextFilters.source_channel) params.set("source_channel", nextFilters.source_channel);
    if (nextFilters.agent_profile) params.set("agent_profile", nextFilters.agent_profile);
    if (nextFilters.status) params.set("status", nextFilters.status);
    if (nextFilters.keyword) params.set("keyword", nextFilters.keyword);
    setSearchParams(params);
    try {
      const res = await fetchLogs(params);
      setItems(res.items);
      setTotal(res.total);
      setPage(nextPage);
      setStats(res.stats);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载日志失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    const savedFiltersRaw = window.localStorage.getItem(SAVED_LOG_FILTER_KEY);
    const savedFilters = savedFiltersRaw ? (JSON.parse(savedFiltersRaw) as typeof filters) : {};
    const next = {
      ...savedFilters,
      ...initialFilters
    };
    setFilters(next);
    form.setFieldsValue(next);
    void load(1, next);
  }, [timeRange, customRange]);

  const openLogDrawer = async (item: ApiCallItem) => {
    const detail = await fetchLogDetail(item.run_id);
    openDetailDrawer({
      title: `执行详情 · ${item.run_id}`,
      subtitle: `${item.route} · ${item.created_at}`,
      width: 760,
      content: (
        <Tabs
          items={[
            {
              key: "summary",
              label: "基本信息",
              children: <JsonViewer value={detail.summary || detail.api_call} />
            },
            {
              key: "request",
              label: "请求参数",
              children: <JsonViewer value={detail.api_call?.request_json || {}} />
            },
            {
              key: "response",
              label: "响应结果",
              children: <JsonViewer value={detail.api_call?.response_json || {}} />
            },
            {
              key: "tools",
              label: "工具调用链",
              children: <JsonViewer value={detail.tool_invocations || []} maxHeight={420} />
            },
            {
              key: "errors",
              label: "错误堆栈",
              children: <JsonViewer value={detail.errors || []} maxHeight={420} />
            },
            {
              key: "trace",
              label: "Trace 时间线",
              children: <JsonViewer value={detail.trace || []} maxHeight={420} />
            }
          ]}
        />
      )
    });
  };

  const columns: ColumnsType<ApiCallItem> = [
    {
      title: "run_id",
      dataIndex: "run_id",
      width: 250,
      ellipsis: true,
      render: (value: string) => (
        <Button
          type="link"
          onClick={(event) => {
            event.stopPropagation();
            void openLogDrawer({ run_id: value } as ApiCallItem);
          }}
          title={value}
        >
          <span
            style={{
              maxWidth: 220,
              display: "inline-block",
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
              verticalAlign: "bottom"
            }}
          >
            {value}
          </span>
        </Button>
      )
    },
    {
      title: "session_id",
      dataIndex: "session_id",
      ellipsis: true,
      width: 260,
      render: (value?: string) => (
        <Button
          type="link"
          onClick={(event) => {
            event.stopPropagation();
            if (value) {
              navigate(`/sessions/${encodeURIComponent(value)}`);
            }
          }}
        >
          <TruncatedText value={value} />
        </Button>
      )
    },
    {
      title: "source_channel",
      dataIndex: "source_channel",
      width: 140,
      render: (value?: string) => value || "-"
    },
    {
      title: "agent_profile",
      dataIndex: "agent_profile",
      width: 170,
      render: (value?: string) => value || "-"
    },
    {
      title: "user_id",
      dataIndex: "user_id",
      width: 220,
      ellipsis: true,
      render: (value?: string) => <TruncatedText value={value} />
    },
    { title: "route", dataIndex: "route", width: 120 },
    {
      title: "status",
      dataIndex: "status",
      width: 110,
      render: (value: string) => <StatusTag status={value} />
    },
    {
      title: "latency",
      dataIndex: "latency_ms",
      width: 120,
      render: (value?: number) => formatDuration(value),
      sorter: (a, b) => (a.latency_ms || 0) - (b.latency_ms || 0)
    },
    {
      title: "created_at",
      dataIndex: "created_at",
      width: 220,
      ellipsis: true,
      render: (value?: string) => <TruncatedText value={value} />
    }
  ];

  const exportCurrent = () => {
    const blob = new Blob([JSON.stringify(items, null, 2)], { type: "application/json" });
    const url = window.URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `logs-${Date.now()}.json`;
    anchor.click();
    window.URL.revokeObjectURL(url);
  };

  return (
    <>
      <PageHeader
        title="请求与执行日志"
        description="按时间、状态、渠道、Agent Profile、关键词等维度检索 Agent 请求，右侧抽屉查看完整请求、响应、工具链与错误。"
        extra={<Typography.Text type="secondary">环境：{environmentLabel}</Typography.Text>}
        actions={[
          { key: "export", label: "导出", onClick: exportCurrent },
          {
            key: "save-filter",
            label: "保存筛选",
            onClick: () => {
              window.localStorage.setItem(SAVED_LOG_FILTER_KEY, JSON.stringify(filters));
              message.success("已保存当前筛选");
            }
          }
        ]}
      />

      <ContextBar>
        <Form
          form={form}
          layout="vertical"
          initialValues={initialFilters}
          style={{ width: "100%" }}
          onFinish={(vals) => {
            const next = {
              route: vals.route || undefined,
              session_id: vals.session_id || undefined,
              user_id: vals.user_id || undefined,
              source_channel: vals.source_channel || undefined,
              agent_profile: vals.agent_profile || undefined,
              status: vals.status || undefined,
              keyword: vals.keyword || undefined
            };
            setFilters(next);
            void load(1, next);
          }}
        >
          <Row gutter={12}>
            <Col xs={24} md={8} lg={6}><Form.Item name="status" label="状态"><Select allowClear options={[{ value: "success", label: "success" }, { value: "streaming", label: "streaming" }, { value: "error", label: "error" }, { value: "timeout", label: "timeout" }]} /></Form.Item></Col>
            <Col xs={24} md={8} lg={6}><Form.Item name="route" label="路由"><Select allowClear options={[{ value: "/run", label: "/run" }, { value: "/stream_run", label: "/stream_run" }]} /></Form.Item></Col>
            <Col xs={24} md={8} lg={6}><Form.Item name="source_channel" label="渠道"><Input placeholder="websdk / admin_panel" /></Form.Item></Col>
            <Col xs={24} md={8} lg={6}><Form.Item name="agent_profile" label="Agent Profile"><Select allowClear options={[{ value: "customer_support", label: "customer_support" }, { value: "employee_assistant", label: "employee_assistant" }]} /></Form.Item></Col>
            <Col xs={24} md={8} lg={6}><Form.Item name="user_id" label="用户"><Input placeholder="user_id" /></Form.Item></Col>
            <Col xs={24} md={8} lg={6}><Form.Item name="session_id" label="会话"><Input placeholder="session_id" /></Form.Item></Col>
            <Col xs={24} md={8} lg={6}><Form.Item name="keyword" label="关键词"><Input placeholder="trace / 问题关键词 / run_id" /></Form.Item></Col>
            <Col xs={24} md={8} lg={6} style={{ display: "flex", alignItems: "end", gap: 8 }}>
              <Button type="primary" htmlType="submit">查询</Button>
              <Button
                onClick={() => {
                  form.resetFields();
                  const next = {};
                  setFilters(next);
                  window.localStorage.removeItem(SAVED_LOG_FILTER_KEY);
                  void load(1, next);
                }}
              >
                重置
              </Button>
            </Col>
          </Row>
        </Form>
      </ContextBar>

      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        <Col xs={24} md={8} xl={4}><Card bordered={false}><Typography.Text type="secondary">请求数</Typography.Text><div style={{ fontSize: 28, fontWeight: 700 }}>{stats.request_count}</div></Card></Col>
        <Col xs={24} md={8} xl={4}><Card bordered={false}><Typography.Text type="secondary">失败数</Typography.Text><div style={{ fontSize: 28, fontWeight: 700, color: "#dc2626" }}>{stats.failure_count}</div></Card></Col>
        <Col xs={24} md={8} xl={4}><Card bordered={false}><Typography.Text type="secondary">超时数</Typography.Text><div style={{ fontSize: 28, fontWeight: 700, color: "#ea580c" }}>{stats.timeout_count}</div></Card></Col>
        <Col xs={24} md={8} xl={4}><Card bordered={false}><Typography.Text type="secondary">平均延迟</Typography.Text><div style={{ fontSize: 28, fontWeight: 700 }}>{stats.avg_latency_ms}ms</div></Card></Col>
        <Col xs={24} md={8} xl={4}><Card bordered={false}><Typography.Text type="secondary">流式占比</Typography.Text><div style={{ fontSize: 28, fontWeight: 700 }}>{stats.stream_ratio}%</div></Card></Col>
        <Col xs={24} md={8} xl={4}><Card bordered={false}><Typography.Text type="secondary">批量选中</Typography.Text><div style={{ fontSize: 28, fontWeight: 700 }}>{selectedRowKeys.length}</div></Card></Col>
      </Row>

      {error ? <ErrorState title="日志列表加载失败" error={error} onRetry={() => void load(page, filters)} /> : null}

      <Card title="调用日志" bordered={false}>
        <Table<ApiCallItem>
          rowKey="id"
          loading={loading}
          columns={columns}
          dataSource={items}
          locale={{
            emptyText: loading ? "加载中..." : <EmptyState title="暂无日志数据" description="调整时间范围或筛选条件后重试。" />
          }}
          rowSelection={{
            selectedRowKeys,
            onChange: (keys) => setSelectedRowKeys(keys)
          }}
          onRow={(record) => ({
            onClick: () => void openLogDrawer(record)
          })}
          pagination={{
            current: page,
            total,
            pageSize: 20,
            onChange: (next) => void load(next)
          }}
          scroll={{ x: 1400 }}
          tableLayout="fixed"
        />
      </Card>
    </>
  );
}
