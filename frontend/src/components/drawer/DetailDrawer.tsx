import { Drawer, Space, Typography } from "antd";

import { useAdminShell } from "../../layouts/AdminShell";

export function DetailDrawer() {
  const { detailDrawer, closeDetailDrawer } = useAdminShell();

  return (
    <Drawer
      title={
        <Space direction="vertical" size={0}>
          <Typography.Text strong>{detailDrawer.title}</Typography.Text>
          {detailDrawer.subtitle ? <Typography.Text type="secondary">{detailDrawer.subtitle}</Typography.Text> : null}
        </Space>
      }
      width={detailDrawer.width || 640}
      open={detailDrawer.open}
      onClose={closeDetailDrawer}
      destroyOnClose
    >
      {detailDrawer.content}
    </Drawer>
  );
}
