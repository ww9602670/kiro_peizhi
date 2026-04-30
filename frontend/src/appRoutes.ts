export type OperatorRouteKey = 'dashboard' | 'accounts' | 'strategies' | 'alerts' | 'me';
export type AdminRouteKey = 'admin-dashboard' | 'operators' | 'alerts';
export type AppRouteKey = OperatorRouteKey | AdminRouteKey | 'login';

export const OPERATOR_ROUTE_PATHS: Record<OperatorRouteKey, string> = {
  dashboard: '/dashboard',
  accounts: '/accounts',
  strategies: '/strategies',
  alerts: '/alerts',
  me: '/me',
};

export const ADMIN_ROUTE_PATHS: Record<AdminRouteKey, string> = {
  'admin-dashboard': '/admin/dashboard',
  operators: '/operators',
  alerts: '/alerts',
};

export function getDefaultRouteKey(isAdmin: boolean): OperatorRouteKey | AdminRouteKey {
  return isAdmin ? 'admin-dashboard' : 'dashboard';
}

export function getRoutePath(key: AppRouteKey): string {
  if (key === 'login') {
    return '/login';
  }

  if (key in OPERATOR_ROUTE_PATHS) {
    return OPERATOR_ROUTE_PATHS[key as OperatorRouteKey];
  }

  return ADMIN_ROUTE_PATHS[key as AdminRouteKey];
}

export function resolveRouteKey(
  pathname: string,
  isAuthenticated: boolean,
  isAdmin: boolean,
): AppRouteKey {
  if (!isAuthenticated) {
    return 'login';
  }

  if (isAdmin) {
    if (pathname === ADMIN_ROUTE_PATHS.alerts) {
      return 'alerts';
    }
    if (pathname === ADMIN_ROUTE_PATHS.operators) {
      return 'operators';
    }
    return 'admin-dashboard';
  }

  switch (pathname) {
    case OPERATOR_ROUTE_PATHS.accounts:
      return 'accounts';
    case OPERATOR_ROUTE_PATHS.strategies:
      return 'strategies';
    case OPERATOR_ROUTE_PATHS.alerts:
      return 'alerts';
    case OPERATOR_ROUTE_PATHS.me:
      return 'me';
    case OPERATOR_ROUTE_PATHS.dashboard:
    default:
      return 'dashboard';
  }
}
