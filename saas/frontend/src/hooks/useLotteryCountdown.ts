/**
 * Lottery countdown hook
 * 
 * Note: For production, recommend using React Query or SWR to ensure single polling source.
 * Current implementation creates separate polling instances per component.
 */
import { useState, useEffect } from 'react';
import { fetchCurrentInstall } from '@/api/lottery';
import type { CurrentInstall } from '@/types/api/lottery';

export function useLotteryCountdown() {
  const [data, setData] = useState<CurrentInstall | null>(null);
  const [closeCountdown, setCloseCountdown] = useState(0);
  const [openCountdown, setOpenCountdown] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdateTime, setLastUpdateTime] = useState<Date | null>(null);
  
  // Fetch latest data every 5 seconds
  useEffect(() => {
    const fetchData = async () => {
      try {
        const resp = await fetchCurrentInstall();
        const result = resp.data;
        if (!result) {
          setError('数据为空');
          return;
        }
        setData(result);
        setCloseCountdown(result.close_countdown_sec);
        setOpenCountdown(result.open_countdown_sec);
        setError(null);
        setLastUpdateTime(new Date());
      } catch (err) {
        console.error('Failed to fetch install info:', err);
        setError('数据延迟');
      }
    };
    
    fetchData();
    const interval = setInterval(fetchData, 5000);
    return () => clearInterval(interval);
  }, []);
  
  // Update countdown every second (clamp to non-negative)
  useEffect(() => {
    const timer = setInterval(() => {
      setCloseCountdown(prev => Math.max(0, prev - 1));
      setOpenCountdown(prev => Math.max(0, prev - 1));
    }, 1000);
    return () => clearInterval(timer);
  }, []);
  
  return {
    data,
    closeCountdown,
    openCountdown,
    error,
    lastUpdateTime,
  };
}
