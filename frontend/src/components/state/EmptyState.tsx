import { Empty, Typography } from "antd";

interface EmptyStateProps {
  title: string;
  description?: string;
}

export function EmptyState({ title, description }: EmptyStateProps) {
  return (
    <Empty
      image={Empty.PRESENTED_IMAGE_SIMPLE}
      description={
        <div>
          <Typography.Text strong>{title}</Typography.Text>
          {description ? (
            <>
              <br />
              <Typography.Text type="secondary">{description}</Typography.Text>
            </>
          ) : null}
        </div>
      }
    />
  );
}
