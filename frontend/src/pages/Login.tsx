import { useState } from 'react';
import { Alert, Button, Card, Form, Input, Tag, Typography, message } from 'antd';
import { LockOutlined, RocketOutlined, UserOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../store/auth';

export default function Login() {
  const { login } = useAuth();
  const nav = useNavigate();
  const [loading, setLoading] = useState(false);

  const doLogin = async (username: string, password: string) => {
    setLoading(true);
    try {
      await login(username, password);
      message.success('登录成功，进入智工云枢');
      nav('/dashboard');
    } catch (e) {
      message.error((e as Error).message || '登录失败');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center',
      background: 'linear-gradient(135deg,#0b2545 0%,#1677ff 60%,#40a9ff 100%)' }}>
      <Card style={{ width: 400, boxShadow: '0 8px 30px rgba(0,0,0,.3)' }} styles={{ body: { padding: 28 } }}>
        <div style={{ textAlign: 'center', marginBottom: 18 }}>
          <RocketOutlined style={{ fontSize: 40, color: '#1677ff' }} />
          <Typography.Title level={3} style={{ margin: '8px 0 0' }}>智工云枢 ICOPS</Typography.Title>
          <Typography.Text type="secondary">AI 原生工程机械智能运营平台 · MVP</Typography.Text>
        </div>
        <Form layout="vertical" onFinish={(v) => doLogin(v.username, v.password)} initialValues={{ username: 'admin', password: 'icops2026' }}>
          <Form.Item name="username" label="账号"><Input prefix={<UserOutlined />} placeholder="admin" /></Form.Item>
          <Form.Item name="password" label="密码"><Input.Password prefix={<LockOutlined />} placeholder="icops2026" /></Form.Item>
          <Button type="primary" htmlType="submit" block loading={loading}>登 录</Button>
        </Form>
        <Alert style={{ marginTop: 12 }} type="info" showIcon message="演示账号：admin / icops2026（销售 sales、调度 dispatcher、售后 service、矿山客户 mine 同密码）" />
        <div style={{ marginTop: 10, textAlign: 'center' }}>
          <Tag color="orange">离线演示模式 · 全部数值数据库直读</Tag>
        </div>
      </Card>
    </div>
  );
}
