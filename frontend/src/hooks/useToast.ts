/**
 * Toast 消息管理 Hook
 */
import { useCallback, useState } from 'react';
import type { ToastMessage } from '@/components/Toast';

let _nextId = 1;

export function useToast() {
  const [messages, setMessages] = useState<ToastMessage[]>([]);

  const showToast = useCallback((text: string, type: ToastMessage['type'] = 'error') => {
    const id = _nextId++;
    setMessages((prev) => [...prev, { id, text, type }]);
  }, []);

  const removeToast = useCallback((id: number) => {
    setMessages((prev) => prev.filter((m) => m.id !== id));
  }, []);

  return { messages, showToast, removeToast };
}
