import { Button, Result } from 'antd';
import { useNavigate } from 'react-router-dom';

export default function NotFound() {
  const nav = useNavigate();
  return <Result status="404" title="页面不存在" extra={<Button type="primary" onClick={() => nav('/dashboard')}>返回驾驶舱</Button>} />;
}
