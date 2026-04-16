/**
 * 多选玩法选择器
 * - 从 /play-codes API 获取分组数据
 * - 下拉面板支持分组展示 + 多选
 * - 已选项以标签形式展示
 */
import { useEffect, useRef, useState } from 'react';
import { listPlayCodes } from '@/api/play-codes';
import type { PlayCodeGroup } from '@/types/api/play-code';
import './PlayCodeMultiSelect.css';

interface Props {
  value: string[];
  onChange: (codes: string[]) => void;
  disabled?: boolean;
  /** 最大可选数量，默认不限 */
  max?: number;
}

export default function PlayCodeMultiSelect({ value, onChange, disabled, max }: Props) {
  const [groups, setGroups] = useState<PlayCodeGroup[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [open, setOpen] = useState(false);
  const wrapperRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    (async () => {
      try {
        const res = await listPlayCodes();
        setGroups(res.data ?? []);
      } catch {
        setError('加载玩法列表失败');
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  // 点击外部关闭
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  // 构建 key_code → name 映射
  const nameMap: Record<string, string> = {};
  for (const g of groups) {
    for (const item of g.items) {
      nameMap[item.key_code] = item.name;
    }
  }

  const toggle = (code: string) => {
    if (disabled) return;
    if (value.includes(code)) {
      onChange(value.filter(c => c !== code));
    } else {
      if (max && value.length >= max) return;
      onChange([...value, code]);
    }
  };

  const removeTag = (code: string) => {
    if (disabled) return;
    onChange(value.filter(c => c !== code));
  };

  if (loading) return <span className="pcms-loading">加载玩法中...</span>;
  if (error) return <span className="pcms-error">{error}</span>;

  return (
    <div className="pcms-wrapper" ref={wrapperRef}>
      <div
        className={`pcms-trigger ${disabled ? 'pcms-disabled' : ''} ${open ? 'pcms-open' : ''}`}
        onClick={() => !disabled && setOpen(!open)}
        role="combobox"
        aria-expanded={open}
        aria-haspopup="listbox"
        tabIndex={disabled ? -1 : 0}
        onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); if (!disabled) setOpen(!open); } }}
      >
        {value.length === 0 ? (
          <span className="pcms-placeholder">请选择玩法</span>
        ) : (
          <div className="pcms-tags">
            {value.map(code => (
              <span key={code} className="pcms-tag">
                {nameMap[code] || code}
                <button type="button" className="pcms-tag-remove" onClick={e => { e.stopPropagation(); removeTag(code); }}
                  aria-label={`移除 ${nameMap[code] || code}`}>×</button>
              </span>
            ))}
          </div>
        )}
        <span className="pcms-arrow">{open ? '▲' : '▼'}</span>
      </div>

      {open && (
        <div className="pcms-dropdown" role="listbox" aria-multiselectable="true">
          {groups.map(g => (
            <div key={g.group_name} className="pcms-group">
              <div className="pcms-group-label">{g.group_name}</div>
              <div className="pcms-group-items">
                {g.items.map(item => {
                  const selected = value.includes(item.key_code);
                  const atMax = !selected && !!max && value.length >= max;
                  return (
                    <div
                      key={item.key_code}
                      className={`pcms-item ${selected ? 'pcms-item-selected' : ''} ${atMax ? 'pcms-item-disabled' : ''}`}
                      onClick={() => !atMax && toggle(item.key_code)}
                      role="option"
                      aria-selected={selected}
                    >
                      <span className="pcms-check">{selected ? '✓' : ''}</span>
                      {item.name}
                    </div>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
