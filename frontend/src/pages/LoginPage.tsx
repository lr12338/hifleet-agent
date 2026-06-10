import { Button, Card, Form, Input, Space, Typography, message } from "antd";
import { useNavigate } from "react-router-dom";

import { enableSkipLogin, setAdminApiKey } from "../auth/adminAuth";

export function LoginPage() {
  const navigate = useNavigate();

  return (
    <Card title="后台登录" style={{ maxWidth: 480, margin: "40px auto" }}>
      <Typography.Paragraph>
        若服务端配置了 <code>ADMIN_API_KEY</code>，请输入密钥后登录。
      </Typography.Paragraph>
      <Form
        layout="vertical"
        onFinish={(vals: { api_key?: string }) => {
          const apiKey = (vals.api_key || "").trim();
          if (!apiKey) {
            message.error("请输入 API Key");
            return;
          }
          setAdminApiKey(apiKey);
          message.success("登录成功");
          navigate("/dashboard", { replace: true });
        }}
      >
        <Form.Item label="Admin API Key" name="api_key">
          <Input.Password placeholder="输入 x-admin-api-key 的值" />
        </Form.Item>
        <Space>
          <Button type="primary" htmlType="submit">
            使用密钥登录
          </Button>
          <Button
            onClick={() => {
              enableSkipLogin();
              navigate("/dashboard", { replace: true });
            }}
          >
            免密进入
          </Button>
        </Space>
      </Form>
    </Card>
  );
}
