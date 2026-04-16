/**
 * 应用布局组件
 * - 顶栏：应用名 + AlertBadge + 登出
 * - 侧边栏：导航菜单
 * - 内容区
 */

import { type ReactNode } from 'react';
import AlertBadge from '@/components/AlertBadge';
import './Layout.css';

export interface NavItem {
  key: string;
  label: string;
}

interface LayoutProps {
  navItems: NavItem[];
  activeKey: string;
  onNavChange: (key: string) => void;
  unreadAlerts: number;
  onLogout: () => void;
  children: ReactNode;
}

export default function Layout({
  navItems,
  activeKey,
  onNavChange,
  unreadAlerts,
  onLogout,
  children,
}: LayoutProps) {
  return (
    <div className="layout">
      <aside className="layout-sidebar">
        <div className="sidebar-brand">投注平台</div>
        <nav className="sidebar-nav" role="navigation">
          {navItems.map((item) => (
            <button
              key={item.key}
              type="button"
              className={`sidebar-nav-item ${activeKey === item.key ? 'sidebar-nav-active' : ''}`}
              onClick={() => onNavChange(item.key)}
            >
              {item.label}
              {item.key === 'alerts' && <AlertBadge count={unreadAlerts} />}
            </button>
          ))}
        </nav>
        <div className="sidebar-footer">
          <button type="button" className="sidebar-logout" onClick={onLogout}>
            登出
          </button>
        </div>
      </aside>
      <main className="layout-main">{children}</main>
      {/* 移动端底部导航栏 */}
      <nav className="layout-bottom-nav" role="navigation">
        {navItems.map((item) => (
          <button
            key={item.key}
            type="button"
            className={`bottom-nav-item ${activeKey === item.key ? 'bottom-nav-active' : ''}`}
            onClick={() => onNavChange(item.key)}
          >
            {item.label}
            {item.key === 'alerts' && unreadAlerts > 0 && (
              <span className="bottom-nav-badge">{unreadAlerts > 99 ? '99+' : unreadAlerts}</span>
            )}
          </button>
        ))}
      </nav>
    </div>
  );
}
