import { Card, Space } from "antd";
import type { ReactNode } from "react";

interface ContextBarProps {
  children: ReactNode;
}

export function ContextBar({ children }: ContextBarProps) {
  return (
    <Card size="small" className="admin-context-bar">
      <Space wrap size={[12, 12]} style={{ width: "100%" }}>
        {children}
      </Space>
    </Card>
  );
}
