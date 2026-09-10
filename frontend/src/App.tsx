import { Navigate, Route, Routes } from 'react-router-dom';
import { Spin } from 'antd';
import { useAuth } from './store/auth';
import MainLayout from './layouts/MainLayout';
import Login from './pages/Login';
import Dashboard from './pages/Dashboard';
import MapMonitor from './pages/MapMonitor';
import Workspace from './pages/Workspace';
import ChatCenter from './pages/ChatCenter';
import Maintenance from './pages/Maintenance';
import NotFound from './pages/NotFound';

function RequireAuth({ children }: { children: JSX.Element }) {
  const { user, ready } = useAuth();
  if (!ready) return <Spin style={{ display: 'block', margin: '120px auto' }} />;
  if (!user) return <Navigate to="/login" replace />;
  return children;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route
        path="/"
        element={
          <RequireAuth>
            <MainLayout />
          </RequireAuth>
        }
      >
        <Route index element={<Navigate to="/dashboard" replace />} />
        <Route path="dashboard" element={<Dashboard />} />
        <Route path="map" element={<MapMonitor />} />
        <Route path="workspace" element={<Workspace />} />
        <Route path="chat" element={<ChatCenter />} />
        <Route path="maintenance" element={<Maintenance />} />
      </Route>
      <Route path="*" element={<NotFound />} />
    </Routes>
  );
}
