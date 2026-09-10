import { useState } from 'react';
import { Layout, Menu, Tag, Typography, theme } from 'antd';
import {
  DashboardOutlined, EnvironmentOutlined, FileTextOutlined, MessageOutlined,
  ToolOutlined, LogoutOutlined, RocketOutlined,
} from '@ant-design/icons';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import { useAuth } from '../store/auth';
import { api } from '../api';
import { useEffect } from 'react';

const { Sider, Header, Content } = Layout;
const MENU = [
  { key: '/dashboard', icon: <DashboardOutlined />, label: '运营驾驶舱' },
  { key: '/workspace', icon: <FileTextOutlined />, label: '方案工作台' },
  { key: '/map', icon: <EnvironmentOutlined />, label: '设备地图监控' },
  { key: '/maintenance', icon: <ToolOutlined />, label: '智能运维中心' },
  { key: '/chat', icon: <MessageOutlined />, label: '智能对话' },
];

export default function MainLayout() {
  const [collapsed, setCollapsed] = useState(false);
  const [llmMode, setLlmMode] = useState('demo');
  const { user, logout } = useAuth();
  const nav = useNavigate();
  const loc = useLocation();
  const { token } = theme.useToken();

  useEffect(() => {
    api.health().then((h) => setLlmMode(h.llm_mode)).catch(() => undefined);
  }, []);

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider collapsible collapsed={collapsed} onCollapse={setCollapsed} theme="dark" width={216}>
        <div style={{ height: 56, display: 'flex', alignItems: 'center', gap: 8, padding: '0 16px', color: '#fff' }}>
          <RocketOutlined style={{ fontSize: 22, color: token.colorPrimary }} />
          {!collapsed && <span style={{ fontWeight: 700, fontSize: 16, whiteSpace: 'nowrap' }}>智工云枢 ICOPS</span>}
        </div>
        <Menu theme="dark" mode="inline" selectedKeys={[loc.pathname]} items={MENU}
          onClick={(e) => nav(e.key)} />
      </Sider>
      <Layout>
        <Header style={{ background: '#fff', padding: '0 20px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', borderBottom: '1px solid #eee' }}>
          <Typography.Text strong>矿山施工全流程智能运营 · AI 原生平台（MVP 演示版）</Typography.Text>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <Tag color={llmMode === 'demo' ? 'orange' : 'green'}>{llmMode === 'demo' ? '离线演示模式' : `大模型：${llmMode}`}</Tag>
            <Typography.Text>{user?.display_name || user?.username}</Typography.Text>
            <a onClick={logout} style={{ color: 'inherit' }}><LogoutOutlined /> 退出</a>
          </div>
        </Header>
        <Content style={{ margin: 12 }}>
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  );
}
