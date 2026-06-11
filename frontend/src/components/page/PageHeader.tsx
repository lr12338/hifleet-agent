import { Button, Space, Typography } from "antd";
import type { ReactNode } from "react";

interface PageHeaderProps {
  title: string;
  description?: string;
  extra?: ReactNode;
  actions?: Array<{
    key: string;
    label: string;
    onClick?: () => void;
    type?: "primary" | "default";
  }>;
}

export function PageHeader({ title, description, extra, actions = [] }: PageHeaderProps) {
  return (
    <div className="admin-page-header">
      <Space direction="vertical" size={2} className="admin-page-header-main">
        <Typography.Title level={3} style={{ margin: 0 }}>
          {title}
        </Typography.Title>
        {description ? <Typography.Text type="secondary">{description}</Typography.Text> : null}
        {extra}
      </Space>
      <Space className="admin-page-header-actions">
        {actions.map((action) => (
          <Button key={action.key} type={action.type || "default"} onClick={action.onClick}>
            {action.label}
          </Button>
        ))}
      </Space>
    </div>
  );
}
