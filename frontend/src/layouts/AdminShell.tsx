import { createContext, useContext, useMemo, useState, type ReactNode } from "react";
import { AppstoreOutlined, BugOutlined, DashboardOutlined, DatabaseOutlined, ExperimentOutlined, MenuFoldOutlined, MenuUnfoldOutlined, SettingOutlined } from "@ant-design/icons";
import { Button, DatePicker, Dropdown, Input, Layout, Menu, Space, Tag, Typography } from "antd";
import dayjs from "dayjs";
import { Link, Outlet, useLocation, useNavigate } from "react-router-dom";

import { clearAdminApiKey, disableSkipLogin } from "../auth/adminAuth";
import { DetailDrawer } from "../components/drawer/DetailDrawer";

const { Header, Sider, Content } = Layout;
const { RangePicker } = DatePicker;

export type TimeRangePreset = "1h" | "24h" | "7d" | "30d";

interface DetailDrawerState {
  open: boolean;
  title: string;
  subtitle?: string;
  width?: number;
  content?: ReactNode;
}

interface AdminShellContextValue {
  timeRange: TimeRangePreset;
  setTimeRange: (value: TimeRangePreset) => void;
  customRange: [string, string] | null;
  setCustomRange: (value: [string, string] | null) => void;
  openDetailDrawer: (payload: Omit<DetailDrawerState, "open">) => void;
  closeDetailDrawer: () => void;
  detailDrawer: DetailDrawerState;
  environmentLabel: string;
}

const AdminShellContext = createContext<AdminShellContextValue | null>(null);

function inferEnvironmentLabel(): string {
  if (typeof window === "undefined") return "unknown";
  const host = window.location.hostname;
  if (host === "127.0.0.1" || host === "localhost") return "local";
  if (host.startsWith("test") || host.includes("staging")) return "staging";
  return "production";
}

function selectedMenuKey(pathname: string): string {
  if (pathname.startsWith("/logs")) return "logs";
  if (pathname.startsWith("/sessions")) return "sessions";
  if (pathname.startsWith("/chat")) return "chat";
  if (pathname.startsWith("/test")) return "test";
  if (pathname.startsWith("/config")) return "config";
  return "dashboard";
}

export function useAdminShell() {
  const context = useContext(AdminShellContext);
  if (!context) {
    throw new Error("useAdminShell must be used inside AdminShell");
  }
  return context;
}

export function AdminShell() {
  const navigate = useNavigate();
  const location = useLocation();
  const [collapsed, setCollapsed] = useState(false);
  const [timeRange, setTimeRange] = useState<TimeRangePreset>("24h");
  const [customRange, setCustomRange] = useState<[string, string] | null>(null);
  const [detailDrawer, setDetailDrawer] = useState<DetailDrawerState>({
    open: false,
    title: "",
    subtitle: "",
    width: 640
  });

  const contextValue = useMemo<AdminShellContextValue>(
    () => ({
      timeRange,
      setTimeRange,
      customRange,
      setCustomRange,
      openDetailDrawer: (payload) => setDetailDrawer({ ...payload, open: true }),
      closeDetailDrawer: () => setDetailDrawer((prev) => ({ ...prev, open: false })),
      detailDrawer,
      environmentLabel: inferEnvironmentLabel()
    }),
    [customRange, detailDrawer, timeRange]
  );

  const logout = () => {
    clearAdminApiKey();
    disableSkipLogin();
    navigate("/login", { replace: true });
  };

  return (
    <AdminShellContext.Provider value={contextValue}>
      <Layout style={{ minHeight: "100vh", background: "#f5f7fb" }}>
        <Sider
          collapsible
          trigger={null}
          collapsed={collapsed}
          theme="light"
          width={240}
          style={{ borderRight: "1px solid #e5e7eb", background: "#fff" }}
        >
          <div style={{ height: 64, display: "flex", alignItems: "center", padding: collapsed ? "0 16px" : "0 20px", borderBottom: "1px solid #eef2f7" }}>
            <Space direction="vertical" size={0}>
              <Typography.Text strong style={{ fontSize: 16 }}>
                {collapsed ? "AI" : "Agent Ops Console"}
              </Typography.Text>
              {!collapsed ? (
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  商业化运营后台
                </Typography.Text>
              ) : null}
            </Space>
          </div>
          <Menu
            mode="inline"
            selectedKeys={[selectedMenuKey(location.pathname)]}
            style={{ borderInlineEnd: 0, paddingTop: 12 }}
            items={[
              { key: "dashboard", icon: <DashboardOutlined />, label: <Link to="/dashboard">总览</Link> },
              { key: "sessions", icon: <DatabaseOutlined />, label: <Link to="/sessions">会话</Link> },
              { key: "chat", icon: <BugOutlined />, label: <Link to="/chat">调试</Link> },
              { key: "logs", icon: <AppstoreOutlined />, label: <Link to="/logs">日志追踪</Link> },
              { key: "test", icon: <ExperimentOutlined />, label: <Link to="/test">API 调试</Link> },
              { key: "config", icon: <SettingOutlined />, label: <Link to="/config">配置</Link> }
            ]}
          />
        </Sider>
        <Layout>
          <Header style={{ background: "#fff", borderBottom: "1px solid #e5e7eb", padding: "0 20px" }}>
            <Space style={{ width: "100%", justifyContent: "space-between" }}>
              <Space size={16}>
                <Button
                  type="text"
                  icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
                  onClick={() => setCollapsed((prev) => !prev)}
                />
                <Typography.Text strong>HiFleet Agent 管理平台</Typography.Text>
                <Tag color="blue">{contextValue.environmentLabel}</Tag>
                <Tag>默认租户</Tag>
              </Space>
              <Space size={12}>
                <Button.Group>
                  {(["1h", "24h", "7d", "30d"] as TimeRangePreset[]).map((item) => (
                    <Button key={item} type={timeRange === item ? "primary" : "default"} onClick={() => setTimeRange(item)}>
                      {item}
                    </Button>
                  ))}
                </Button.Group>
                <RangePicker
                  size="small"
                  onChange={(values) => {
                    if (!values?.[0] || !values?.[1]) {
                      setCustomRange(null);
                      return;
                    }
                    setCustomRange([values[0].toISOString(), values[1].toISOString()]);
                  }}
                  showTime
                  value={
                    customRange
                      ? [dayjs(customRange[0]), dayjs(customRange[1])]
                      : null
                  }
                />
                <Input.Search placeholder="全局搜索 session / run_id" style={{ width: 240 }} />
                <Dropdown
                  menu={{
                    items: [{ key: "logout", label: "退出登录", onClick: logout }]
                  }}
                >
                  <Button>用户菜单</Button>
                </Dropdown>
              </Space>
            </Space>
          </Header>
          <Content style={{ padding: 20 }}>
            <Outlet />
          </Content>
        </Layout>
        <DetailDrawer />
      </Layout>
    </AdminShellContext.Provider>
  );
}
