/**
 * 确认对话框组件 — 替代原生 window.confirm()
 * 使用 <dialog> 元素，支持键盘操作和无障碍访问
 */
import { useEffect, useRef } from 'react';
import './ConfirmDialog.css';

export interface ConfirmDialogProps {
  open: boolean;
  title?: string;
  message: string;
  confirmText?: string;
  cancelText?: string;
  onConfirm: () => void;
  onCancel: () => void;
}

export default function ConfirmDialog({
  open,
  title = '确认操作',
  message,
  confirmText = '确认',
  cancelText = '取消',
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const el = dialogRef.current;
    if (!el) return;
    if (open && !el.open) el.showModal();
    if (!open && el.open) el.close();
  }, [open]);

  // ESC 关闭
  const handleCancel = (e: React.SyntheticEvent) => {
    e.preventDefault();
    onCancel();
  };

  if (!open) return null;

  return (
    <dialog ref={dialogRef} className="confirm-dialog" onCancel={handleCancel}>
      <div className="confirm-dialog-body">
        <h3 className="confirm-dialog-title">{title}</h3>
        <p className="confirm-dialog-message">{message}</p>
        <div className="confirm-dialog-actions">
          <button type="button" className="confirm-dialog-btn cancel" onClick={onCancel}>
            {cancelText}
          </button>
          <button type="button" className="confirm-dialog-btn confirm" onClick={onConfirm} autoFocus>
            {confirmText}
          </button>
        </div>
      </div>
    </dialog>
  );
}
