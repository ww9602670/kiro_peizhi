import { useEffect, useState } from 'react';
import { useAuth } from '@/hooks/useAuth';
import { useAlerts } from '@/hooks/useAlerts';
import Login from '@/pages/Login';
import Layout, { type NavItem } from '@/components/Layout';
import Dashboard from '@/pages/operator/Dashboard';
import Accounts from '@/pages/operator/Accounts';
import Strategies from '@/pages/operator/Strategies';
import BetOrders from '@/pages/operator/BetOrders';
import Alerts from '@/pages/operator/Alerts';
import AdminDashboardPage from '@/pages/admin/Dashboard';
import Operators from '@/pages/admin/Operators';
import './App.css';

const OPERATOR_NAV: NavItem[] = [
  { key: 'dashboard', label: '仪表盘' },
  { key: 'accounts', label: '账号' },
  { key: 'strategies', label: '策略' },
  { key: 'bet-orders', label: '投注记录' },
  { key: 'alerts', label: '告警' },
];

const ADMIN_NAV: NavItem[] = [
  { key: 'admin-dashboard', label: '仪表盘' },
  { key: 'operators', label: '操作者管理' },
];

function App() {
  const { isAuthenticated, role, logout } = useAuth();
  const { unreadCount, startPolling, stopPolling } = useAlerts();

  const isAdmin = role === 'admin';
  const navItems = isAdmin ? ADMIN_NAV : OPERATOR_NAV;
  const defaultTab = isAdmin ? 'admin-dashboard' : 'dashboard';

  const [activeTab, setActiveTab] = useState(defaultTab);

  useEffect(() => {
    if (isAuthenticated) {
      startPolling();
      setActiveTab(isAdmin ? 'admin-dashboard' : 'dashboard');
    } else {
      stopPolling();
    }
    return () => stopPolling();
  }, [isAuthenticated, isAdmin, startPolling, stopPolling]);

  if (!isAuthenticated) {
    return <Login />;
  }

  return (
    <Layout
      navItems={navItems}
      activeKey={activeTab}
      onNavChange={setActiveTab}
      unreadAlerts={unreadCount}
      onLogout={logout}
    >
      {/* Operator pages */}
      {activeTab === 'dashboard' && <Dashboard />}
      {activeTab === 'accounts' && <Accounts />}
      {activeTab === 'strategies' && <Strategies />}
      {activeTab === 'bet-orders' && <BetOrders />}
      {activeTab === 'alerts' && <Alerts />}

      {/* Admin pages */}
      {activeTab === 'admin-dashboard' && <AdminDashboardPage />}
      {activeTab === 'operators' && <Operators />}
    </Layout>
  );
}

export default App;
