/**
 * 操作者管理页面（管理员）
 * - 操作者列表（分页）
 * - 创建操作者
 * - 修改操作者
 * - 禁用/启用操作者
 */

import { useCallback, useEffect, useState } from 'react';
import { listOperators, createOperator, updateOperatorStatus } from '@/api/admin';
import { isApiError } from '@/api/request';
import type { OperatorInfo } from '@/types/api/operator';
import Toast from '@/components/Toast';
import { useToast } from '@/hooks/useToast';
import './Operators.css';

export default function Operators() {
  const [operators, setOperators] = useState<OperatorInfo[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const pageSize = 20;

  // Create form
  const [showCreate, setShowCreate] = useState(false);
  const [createForm, setCreateForm] = useState({ username: '', password: '', max_accounts: 1, expire_date: '' });
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState('');
  const { messages, showToast, removeToast } = useToast();

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const res = await listOperators({ page, page_size: pageSize });
      if (res.data) {
        setOperators(res.data.items);
        setTotal(res.data.total);
      }
    } catch (err) {
      if (isApiError(err)) setError(err.message);
      else setError('加载失败');
    } finally {
      setLoading(false);
    }
  }, [page]);

  useEffect(() => { load(); }, [load]);

  const handleCreate = async () => {
    setCreating(true);
    setCreateError('');
    try {
      await createOperator({
        username: createForm.username,
        password: createForm.password,
        max_accounts: createForm.max_accounts,
        expire_date: createForm.expire_date || null,
      });
      setShowCreate(false);
      setCreateForm({ username: '', password: '', max_accounts: 1, expire_date: '' });
      await load();
    } catch (err) {
      if (isApiError(err)) setCreateError(err.message);
      else setCreateError('创建失败');
    } finally {
      setCreating(false);
    }
  };

  const handleToggleStatus = async (op: OperatorInfo) => {
    const newStatus = op.status === 'active' ? 'disabled' : 'active';
    try {
      await updateOperatorStatus(op.id, { status: newStatus });
      await load();
    } catch (err) {
      if (isApiError(err)) showToast(err.message);
    }
  };

  const totalPages = Math.ceil(total / pageSize);

  return (
    <div className="operators-page">
      <Toast messages={messages} onRemove={removeToast} />
      <div className="operators-header">
        <h1 className="operators-title">操作者管理</h1>
        <button type="button" className="create-btn" onClick={() => setShowCreate(true)}>
          + 创建操作者
        </button>
      </div>

      {/* Create form */}
      {showCreate && (
        <div className="create-form">
          <h3 className="create-form-title">创建操作者</h3>
          {createError && <div className="form-error">{createError}</div>}
          <label className="form-field">
            <span>用户名</span>
            <input value={createForm.username} onChange={(e) => setCreateForm({ ...createForm, username: e.target.value })} />
          </label>
          <label className="form-field">
            <span>密码</span>
            <input type="password" value={createForm.password} onChange={(e) => setCreateForm({ ...createForm, password: e.target.value })} />
          </label>
          <label className="form-field">
            <span>最大账号数</span>
            <input type="number" min={1} value={createForm.max_accounts} onChange={(e) => setCreateForm({ ...createForm, max_accounts: Number(e.target.value) })} />
          </label>
          <label className="form-field">
            <span>到期日期</span>
            <input type="date" value={createForm.expire_date} onChange={(e) => setCreateForm({ ...createForm, expire_date: e.target.value })} />
          </label>
          <div className="form-actions">
            <button type="button" className="submit-btn" onClick={handleCreate} disabled={creating}>
              {creating ? '创建中...' : '确认创建'}
            </button>
            <button type="button" className="cancel-btn" onClick={() => setShowCreate(false)}>取消</button>
          </div>
        </div>
      )}

      {error && <div className="operators-error">{error}</div>}

      {loading ? (
        <p className="loading-text">加载中...</p>
      ) : operators.length > 0 ? (
        <>
          <div className="operators-table-wrap">
            <table className="operators-table">
              <thead>
                <tr>
                  <th>ID</th>
                  <th>用户名</th>
                  <th>角色</th>
                  <th>状态</th>
                  <th>最大账号</th>
                  <th>到期日期</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {operators.map((op) => (
                  <tr key={op.id}>
                    <td data-label="ID">{op.id}</td>
                    <td data-label="用户名">{op.username}</td>
                    <td data-label="角色">{op.role === 'admin' ? '管理员' : '操作者'}</td>
                    <td data-label="状态">
                      <span className={`op-status op-status-${op.status}`}>
                        {op.status === 'active' ? '活跃' : op.status === 'disabled' ? '禁用' : op.status}
                      </span>
                    </td>
                    <td data-label="最大账号">{op.max_accounts}</td>
                    <td data-label="到期日期">{op.expire_date ?? '-'}</td>
                    <td data-label="操作">
                      {op.role !== 'admin' && (
                        <button
                          type="button"
                          className={`toggle-btn ${op.status === 'active' ? 'toggle-disable' : 'toggle-enable'}`}
                          onClick={() => handleToggleStatus(op)}
                        >
                          {op.status === 'active' ? '禁用' : '启用'}
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {totalPages > 1 && (
            <div className="operators-pagination">
              <button type="button" className="page-btn" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>上一页</button>
              <span className="page-info">{page} / {totalPages}</span>
              <button type="button" className="page-btn" disabled={page >= totalPages} onClick={() => setPage((p) => p + 1)}>下一页</button>
            </div>
          )}
        </>
      ) : (
        <p className="empty-text">暂无操作者</p>
      )}
    </div>
  );
}
