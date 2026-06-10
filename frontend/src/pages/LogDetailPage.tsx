import { useEffect, useState } from "react";
import { Card, Col, Row, Tabs, Typography } from "antd";
import { useParams } from "react-router-dom";

import { fetchLogDetail } from "../api/client";
import type { LogDetailResponse } from "../api/client";
import { JsonViewer } from "../components/common/JsonViewer";
import { StatusTag } from "../components/common/StatusTag";
import { PageHeader } from "../components/page/PageHeader";
import { LoadingState } from "../components/state/LoadingState";

const { Paragraph } = Typography;

export function LogDetailPage() {
  const { runId = "" } = useParams();
  const [detail, setDetail] = useState<LogDetailResponse | null>(null);

  useEffect(() => {
    if (!runId) return;
    void fetchLogDetail(runId).then(setDetail);
  }, [runId]);

  return (
    <>
      <PageHeader title={`调用详情 ${runId}`} description="查看本次请求的摘要、请求参数、响应结果、工具链与错误堆栈。" />
      {!detail ? <LoadingState /> : null}
      {detail ? (
        <>
          <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
            <Col xs={24} md={6}><Card bordered={false}><Typography.Text type="secondary">状态</Typography.Text><div><StatusTag status={detail.api_call?.status} /></div></Card></Col>
            <Col xs={24} md={6}><Card bordered={false}><Typography.Text type="secondary">路由</Typography.Text><div>{detail.api_call?.route || "-"}</div></Card></Col>
            <Col xs={24} md={6}><Card bordered={false}><Typography.Text type="secondary">延迟</Typography.Text><div>{detail.api_call?.latency_ms || 0}ms</div></Card></Col>
            <Col xs={24} md={6}><Card bordered={false}><Typography.Text type="secondary">工具 / 错误</Typography.Text><div>{detail.tool_invocations.length} / {detail.errors.length}</div></Card></Col>
          </Row>
          <Card bordered={false}>
            <Tabs
              items={[
                {
                  key: "summary",
                  label: "Summary",
                  children: <JsonViewer value={detail.summary || detail.api_call} maxHeight={420} />
                },
                {
                  key: "request",
                  label: "Request/Response",
                  children: (
                    <Paragraph>
                      <JsonViewer value={detail.api_call} maxHeight={420} />
                    </Paragraph>
                  )
                },
                {
                  key: "tools",
                  label: "Tools",
                  children: (
                    <Paragraph>
                      <JsonViewer value={detail.tool_invocations || []} maxHeight={420} />
                    </Paragraph>
                  )
                },
                {
                  key: "errors",
                  label: "Errors",
                  children: (
                    <Paragraph>
                      <JsonViewer value={detail.errors || []} maxHeight={420} />
                    </Paragraph>
                  )
                },
                {
                  key: "trace",
                  label: "Trace",
                  children: <JsonViewer value={detail.trace || []} maxHeight={420} />
                }
              ]}
            />
          </Card>
        </>
      ) : null}
    </>
  );
}
