import { Alert, Button, Space, Typography } from "antd";

interface ErrorStateProps {
  title: string;
  error?: string;
  onRetry?: () => void;
}

export function ErrorState({ title, error, onRetry }: ErrorStateProps) {
  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      <Alert
        type="error"
        showIcon
        message={title}
        description={error || "请求失败，请稍后重试"}
        action={onRetry ? <Button onClick={onRetry}>重试</Button> : undefined}
      />
      <Typography.Text type="secondary">你也可以调整筛选条件后重新查询。</Typography.Text>
    </Space>
  );
}
