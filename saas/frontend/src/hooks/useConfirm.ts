/**
 * 确认对话框 Hook — 替代 window.confirm()
 * 返回 Promise，用法：const ok = await confirm('确定删除？');
 */
import { useCallback, useRef, useState } from 'react';

interface ConfirmState {
  open: boolean;
  message: string;
  title?: string;
}

export function useConfirm() {
  const [state, setState] = useState<ConfirmState>({ open: false, message: '' });
  const resolveRef = useRef<((value: boolean) => void) | null>(null);

  const confirm = useCallback((message: string, title?: string): Promise<boolean> => {
    return new Promise((resolve) => {
      resolveRef.current = resolve;
      setState({ open: true, message, title });
    });
  }, []);

  const handleConfirm = useCallback(() => {
    setState({ open: false, message: '' });
    resolveRef.current?.(true);
    resolveRef.current = null;
  }, []);

  const handleCancel = useCallback(() => {
    setState({ open: false, message: '' });
    resolveRef.current?.(false);
    resolveRef.current = null;
  }, []);

  return {
    confirmState: state,
    confirm,
    handleConfirm,
    handleCancel,
  };
}
