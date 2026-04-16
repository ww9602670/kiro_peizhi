/**
 * Toast 提示组件 — 替代原生 window.alert()
 * 自动消失，支持 success / error / info 类型
 */
import { useEffect } from 'react';
import './Toast.css';

export interface ToastMessage {
  id: number;
  text: string;
  type: 'success' | 'error' | 'info';
}

interface ToastProps {
  messages: ToastMessage[];
  onRemove: (id: number) => void;
}

export default function Toast({ messages, onRemove }: ToastProps) {
  if (messages.length === 0) return null;

  return (
    <div className="toast-container" aria-live="polite">
      {messages.map((msg) => (
        <ToastItem key={msg.id} message={msg} onRemove={onRemove} />
      ))}
    </div>
  );
}

function ToastItem({ message, onRemove }: { message: ToastMessage; onRemove: (id: number) => void }) {
  useEffect(() => {
    const timer = setTimeout(() => onRemove(message.id), 3000);
    return () => clearTimeout(timer);
  }, [message.id, onRemove]);

  return (
    <div className={`toast-item toast-${message.type}`} role="alert">
      <span className="toast-text">{message.text}</span>
      <button type="button" className="toast-close" onClick={() => onRemove(message.id)} aria-label="关闭">
        ×
      </button>
    </div>
  );
}
