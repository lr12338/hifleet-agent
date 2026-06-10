import { Skeleton, Space } from "antd";

export function LoadingState() {
  return (
    <Space direction="vertical" style={{ width: "100%" }} size={16}>
      <Skeleton active paragraph={{ rows: 2 }} />
      <Skeleton active paragraph={{ rows: 4 }} />
      <Skeleton active paragraph={{ rows: 6 }} />
    </Space>
  );
}
