import { Card, Col, Row, Typography } from "antd";

import { ContextBar } from "../components/page/ContextBar";
import { PageHeader } from "../components/page/PageHeader";

export function ConfigPage() {
  return (
    <>
      <PageHeader
        title="配置中心"
        description="集中管理后台运行环境、接口接入与后续告警、模板、分享策略等配置。"
      />
      <ContextBar>
        <Typography.Text type="secondary">当前版本先提供骨架入口，后续可扩展告警规则、分享权限、筛选视图与模板配置。</Typography.Text>
      </ContextBar>
      <Row gutter={[16, 16]}>
        <Col span={12}>
          <Card title="环境配置" bordered={false}>
            <Typography.Text type="secondary">支持展示环境标识、默认租户、Agent 基础参数与版本信息。</Typography.Text>
          </Card>
        </Col>
        <Col span={12}>
          <Card title="平台配置" bordered={false}>
            <Typography.Text type="secondary">后续可扩展导出策略、分享权限、告警接收人和默认筛选视图。</Typography.Text>
          </Card>
        </Col>
      </Row>
    </>
  );
}
