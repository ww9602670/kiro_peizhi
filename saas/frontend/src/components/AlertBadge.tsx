/**
 * 未读告警徽标组件
 * - 显示未读告警数量
 * - count=0 时隐藏徽标
 * - count>99 时显示 99+
 */

import './AlertBadge.css';

interface AlertBadgeProps {
  count: number;
}

export default function AlertBadge({ count }: AlertBadgeProps) {
  if (count <= 0) return null;

  const display = count > 99 ? '99+' : String(count);

  return (
    <span className="alert-badge" aria-label={`${count} 条未读告警`}>
      {display}
    </span>
  );
}
