import { useContext } from 'react';
import { AlertsContext, type AlertsContextValue } from '@/contexts/alertsContextValue';

export function useAlertsContext(): AlertsContextValue {
  const ctx = useContext(AlertsContext);
  if (!ctx) {
    throw new Error('useAlertsContext must be used within <AlertsProvider>');
  }
  return ctx;
}
