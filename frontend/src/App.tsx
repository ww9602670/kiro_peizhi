import { useCallback, useEffect, useState } from 'react';
import { getRoutePath, resolveRouteKey, type AppRouteKey } from '@/appRoutes';
import Layout, { type NavItem } from '@/components/Layout';
import { AlertsProvider } from '@/contexts/AlertsContext';
import { useAlertsContext } from '@/hooks/useAlertsContext';
import { useAuth } from '@/hooks/useAuth';
import Login from '@/pages/Login';
import AdminDashboardPage from '@/pages/admin/Dashboard';
import Operators from '@/pages/admin/Operators';
import Accounts from '@/pages/operator/Accounts';
import Dashboard from '@/pages/operator/Dashboard';
import My from '@/pages/operator/My';
import Strategies from '@/pages/operator/Strategies';
import './App.css';

export type StrategyCreateIntent = {
  accountId?: number;
  nonce: number;
};

const OPERATOR_NAV: NavItem[] = [
  { key: 'dashboard', label: '仪表盘' },
  { key: 'accounts', label: '账号' },
  { key: 'strategies', label: '策略' },
  { key: 'me', label: '我的' },
];

const ADMIN_NAV: NavItem[] = [
  { key: 'admin-dashboard', label: '仪表盘' },
  { key: 'operators', label: '操作员' },
];

interface AuthedAppShellProps {
  isAdmin: boolean;
  navItems: NavItem[];
  activeTab: AppRouteKey;
  onNavChange: (key: string) => void;
  onLogout: () => Promise<void> | void;
  strategyCreateIntent: StrategyCreateIntent | null;
  clearStrategyIntent: () => void;
  openStrategyCreate: (accountId?: number) => void;
}

function AuthedAppShell({
  isAdmin,
  navItems,
  activeTab,
  onNavChange,
  onLogout,
  strategyCreateIntent,
  clearStrategyIntent,
  openStrategyCreate,
}: AuthedAppShellProps) {
  const { unreadCount } = useAlertsContext();

  return (
    <Layout
      navItems={navItems}
      activeKey={activeTab}
      onNavChange={onNavChange}
      unreadAlerts={unreadCount}
      onLogout={onLogout}
    >
      {activeTab === 'dashboard' && <Dashboard onCreateStrategy={() => openStrategyCreate()} />}
      {activeTab === 'accounts' && <Accounts onCreateStrategy={(accountId) => openStrategyCreate(accountId)} />}
      {activeTab === 'strategies' && (
        <Strategies
          createIntent={strategyCreateIntent}
          onCreateIntentConsumed={clearStrategyIntent}
        />
      )}
      {activeTab === 'me' && <My />}

      {isAdmin && activeTab === 'admin-dashboard' && <AdminDashboardPage />}
      {isAdmin && activeTab === 'operators' && <Operators />}
    </Layout>
  );
}

function App() {
  const { isAuthenticated, role, logout } = useAuth();
  const isAdmin = role === 'admin';
  const navItems = isAdmin ? ADMIN_NAV : OPERATOR_NAV;

  const [activeTab, setActiveTab] = useState<AppRouteKey>(() =>
    resolveRouteKey(window.location.pathname, isAuthenticated, isAdmin),
  );
  const [strategyCreateIntent, setStrategyCreateIntent] = useState<StrategyCreateIntent | null>(null);

  const syncRoute = useCallback(() => {
    setActiveTab(resolveRouteKey(window.location.pathname, isAuthenticated, isAdmin));
  }, [isAuthenticated, isAdmin]);

  const navigateTo = useCallback(
    (nextKey: AppRouteKey, options?: { replace?: boolean }) => {
      const targetPath = getRoutePath(nextKey);
      if (window.location.pathname !== targetPath) {
        if (options?.replace) {
          window.history.replaceState({}, '', targetPath);
        } else {
          window.history.pushState({}, '', targetPath);
        }
      }
      setActiveTab(nextKey);
    },
    [],
  );

  useEffect(() => {
    const handlePopState = () => syncRoute();
    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  }, [syncRoute]);

  useEffect(() => {
    if (!isAuthenticated) {
      navigateTo('login', { replace: true });
      return;
    }

    const resolvedKey = resolveRouteKey(window.location.pathname, true, isAdmin);
    const expectedPath = getRoutePath(resolvedKey);
    if (window.location.pathname !== expectedPath) {
      navigateTo(resolvedKey, { replace: true });
      return;
    }
    setActiveTab(resolvedKey);
  }, [isAuthenticated, isAdmin, navigateTo]);

  const handleNavChange = (key: string) => {
    navigateTo(key as AppRouteKey);
  };

  const openStrategyCreate = (accountId?: number) => {
    setStrategyCreateIntent({ accountId, nonce: Date.now() });
    navigateTo('strategies');
  };

  const handleLogout = async () => {
    await logout();
    setStrategyCreateIntent(null);
    navigateTo('login', { replace: true });
  };

  if (!isAuthenticated) {
    return <Login />;
  }

  return (
    <AlertsProvider>
      <AuthedAppShell
        isAdmin={isAdmin}
        navItems={navItems}
        activeTab={activeTab}
        onNavChange={handleNavChange}
        onLogout={handleLogout}
        strategyCreateIntent={strategyCreateIntent}
        clearStrategyIntent={() => setStrategyCreateIntent(null)}
        openStrategyCreate={openStrategyCreate}
      />
    </AlertsProvider>
  );
}

export default App;
