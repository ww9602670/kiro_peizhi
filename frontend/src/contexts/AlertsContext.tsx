import { useEffect, type ReactNode } from 'react';
import { useAlerts } from '@/hooks/useAlerts';
import { AlertsContext } from './alertsContextValue';

export function AlertsProvider({ children }: { children: ReactNode }) {
  const value = useAlerts();
  const { startPolling, stopPolling } = value;

  useEffect(() => {
    startPolling();
    return () => stopPolling();
  }, [startPolling, stopPolling]);

  return <AlertsContext.Provider value={value}>{children}</AlertsContext.Provider>;
}
