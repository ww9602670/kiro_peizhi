/** 分组玩法选择器 */

import { useEffect, useState } from 'react';
import { listPlayCodes } from '@/api/play-codes';
import type { PlayCodeGroup } from '@/types/api/play-code';
import './PlayCodeSelect.css';

interface Props {
  value: string;
  onChange: (keyCode: string) => void;
  disabled?: boolean;
  platformType?: string;
}

export default function PlayCodeSelect({ value, onChange, disabled, platformType }: Props) {
  const [groups, setGroups] = useState<PlayCodeGroup[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const fetchGroups = async () => {
    setLoading(true);
    setError('');
    try {
      const res = await listPlayCodes({ platformType });
      setGroups(res.data ?? []);
    } catch {
      setError('加载玩法列表失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchGroups(); }, [platformType]);

  if (loading) {
    return <span className="play-code-loading">加载玩法中...</span>;
  }

  if (error) {
    return (
      <span className="play-code-error">
        {error}
        <button type="button" className="play-code-retry" onClick={fetchGroups}>重试</button>
      </span>
    );
  }

  return (
    <select
      className="play-code-select"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      disabled={disabled}
      aria-label="选择玩法"
    >
      <option value="">-- 请选择玩法 --</option>
      {groups.map((g) => (
        <optgroup key={g.group_name} label={g.group_name}>
          {g.items.map((item) => (
            <option key={item.key_code} value={item.key_code}>
              {item.name}
            </option>
          ))}
        </optgroup>
      ))}
    </select>
  );
}
