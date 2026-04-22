import { createContext } from 'react';
import type { useAlerts } from '@/hooks/useAlerts';

export type AlertsContextValue = ReturnType<typeof useAlerts>;

export const AlertsContext = createContext<AlertsContextValue | null>(null);
