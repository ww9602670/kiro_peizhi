/**
 * 操作者管理页面（管理员）
 * - 操作者列表（分页）
 * - 创建操作者
 * - 修改操作者
 * - 禁用/启用操作者
 */

import { useCallback, useEffect, useState } from 'react';
import {
  listOperators,
  createOperator,
  updateOperatorStatus,
  listOperatorStrategyPermissions,
  updateOperatorStrategyPermissions,
  listSharedMarketUncoveredUrls,
  listSharedMarketGroups,
  ignoreSharedMarketUncoveredUrl,
  recheckSharedMarketUncoveredUrl,
  joinSharedMarketUncoveredUrlGroup,
} from '@/api/admin';
import { isApiError } from '@/api/request';
import type { OperatorInfo } from '@/types/api/operator';
import type {
  OperatorStrategyPermissionInfo,
  SharedMarketGroupInfo,
  SharedMarketUncoveredUrlInfo,
  StrategyPermissionType,
} from '@/types/api/strategy';
import Toast from '@/components/Toast';
import { useToast } from '@/hooks/useToast';
import './Operators.css';

const STRATEGY_PERMISSION_OPTIONS: Array<{ value: StrategyPermissionType; label: string }> = [
  { value: 'ai_same_random_flat', label: 'AI推荐同号平注' },
  { value: 'ai_same_random_martin', label: 'AI推荐同号平注马丁' },
  { value: 'flat', label: '平注' },
  { value: 'martin', label: '马丁' },
  { value: 'dw3_flat', label: '三字定位平注' },
  { value: 'dw3_martin', label: '三字定位马丁' },
  { value: 'omission_random_flat', label: '遗漏随机平注' },
  { value: 'omission_random_martin', label: '遗漏随机马丁' },
  { value: 'ai_random_flat', label: 'AI推荐平注' },
  { value: 'ai_random_martin', label: 'AI推荐马丁' },
  { value: 'red_wave_double_martin', label: '红波追双' },
  { value: 'green_wave_single_martin', label: '绿波追单' },
];

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
  const [permissionOperator, setPermissionOperator] = useState<OperatorInfo | null>(null);
  const [permissionInfo, setPermissionInfo] = useState<OperatorStrategyPermissionInfo | null>(null);
  const [permissionLoading, setPermissionLoading] = useState(false);
  const [savingPermission, setSavingPermission] = useState(false);

  // 共享网址待审核
  const [sharedReviewLoading, setSharedReviewLoading] = useState(false);
  const [sharedReviewError, setSharedReviewError] = useState('');
  const [sharedMarketUncoveredRows, setSharedMarketUncoveredRows] = useState<SharedMarketUncoveredUrlInfo[]>([]);
  const [sharedMarketGroups, setSharedMarketGroups] = useState<SharedMarketGroupInfo[]>([]);
  const [sharedReviewActionLoadingId, setSharedReviewActionLoadingId] = useState<number | null>(null);
  const [sharedReviewPage, setSharedReviewPage] = useState(1);
  const [sharedReviewTotal, setSharedReviewTotal] = useState(0);
  const [sharedReviewPageSize] = useState(10);
  const [sharedJoinSelection, setSharedJoinSelection] = useState<Record<number, string>>({});

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

  const loadSharedMarketReview = useCallback(async () => {
    setSharedReviewLoading(true);
    setSharedReviewError('');

    try {
      const groupRes = await listSharedMarketGroups();
      setSharedMarketGroups(groupRes.data ?? []);
    } catch (err) {
      if (isApiError(err)) {
        showToast(`加载共享组失败：${err.message}`);
      } else {
        showToast('加载共享组失败');
      }
      setSharedMarketGroups([]);
    }

    try {
      const res = await listSharedMarketUncoveredUrls({
        page: sharedReviewPage,
        page_size: sharedReviewPageSize,
        status: 'pending',
      });
      if (res.data) {
        setSharedMarketUncoveredRows(res.data.items);
        setSharedReviewTotal(res.data.total);
      }
    } catch (err) {
      if (isApiError(err)) {
        setSharedReviewError(err.message);
      } else {
        setSharedReviewError('加载待审核记录失败');
      }
      setSharedMarketUncoveredRows([]);
      setSharedReviewTotal(0);
    } finally {
      setSharedReviewLoading(false);
    }
  }, [sharedReviewPage, sharedReviewPageSize, showToast]);

  useEffect(() => {
    void loadSharedMarketReview();
  }, [loadSharedMarketReview]);

  const handleToggleStatus = async (op: OperatorInfo) => {
    const newStatus = op.status === 'active' ? 'disabled' : 'active';
    try {
      await updateOperatorStatus(op.id, { status: newStatus });
      await load();
    } catch (err) {
      if (isApiError(err)) showToast(err.message);
    }
  };

  const openPermissionPanel = async (op: OperatorInfo) => {
    setPermissionOperator(op);
    setPermissionInfo(null);
    setPermissionLoading(true);
    try {
      const res = await listOperatorStrategyPermissions(op.id);
      setPermissionInfo(res.data ?? null);
    } catch (err) {
      if (isApiError(err)) showToast(err.message);
      else showToast('加载策略授权失败');
    } finally {
      setPermissionLoading(false);
    }
  };

  const handlePermissionToggle = async (strategyType: StrategyPermissionType) => {
    if (!permissionOperator || !permissionInfo) return;
    const current = new Set(permissionInfo.allowed_strategy_types);
    if (current.has(strategyType)) current.delete(strategyType);
    else current.add(strategyType);
    const nextTypes = STRATEGY_PERMISSION_OPTIONS
      .map((item) => item.value)
      .filter((item) => current.has(item));

    setSavingPermission(true);
    try {
      const res = await updateOperatorStrategyPermissions(
        permissionOperator.id,
        nextTypes
      );
      if (res.data) {
        setPermissionInfo(res.data);
      }
    } catch (err) {
      if (isApiError(err)) showToast(err.message);
      else showToast('保存策略授权失败');
    } finally {
      setSavingPermission(false);
    }
  };

  const updateSharedReviewRow = (nextRow: SharedMarketUncoveredUrlInfo | null | undefined) => {
    if (!nextRow) return;
    setSharedMarketUncoveredRows((previous) =>
      nextRow.status === 'pending'
        ? previous.map((row) => (row.id === nextRow.id ? nextRow : row))
        : previous.filter((row) => row.id !== nextRow.id),
    );
  };

  const handleIgnoreSharedReview = async (recordId: number) => {
    setSharedReviewActionLoadingId(recordId);
    try {
      const res = await ignoreSharedMarketUncoveredUrl(recordId);
      updateSharedReviewRow(res.data);
      showToast('已设置为忽略');
    } catch (err) {
      if (isApiError(err)) showToast(err.message);
      else showToast('操作失败');
    } finally {
      setSharedReviewActionLoadingId(null);
    }
  };

  const handleRecheckSharedReview = async (recordId: number) => {
    setSharedReviewActionLoadingId(recordId);
    try {
      const res = await recheckSharedMarketUncoveredUrl(recordId);
      updateSharedReviewRow(res.data);
      showToast('已标记为重新检测');
    } catch (err) {
      if (isApiError(err)) showToast(err.message);
      else showToast('操作失败');
    } finally {
      setSharedReviewActionLoadingId(null);
    }
  };

  const handleJoinSharedGroup = async (recordId: number) => {
    const selected = sharedJoinSelection[recordId];
    if (!selected) {
      showToast('请先选择共享组');
      return;
    }
    const sharedGroupId = Number(selected);
    if (Number.isNaN(sharedGroupId)) {
      showToast('请先选择共享组');
      return;
    }
    setSharedReviewActionLoadingId(recordId);
    try {
      const res = await joinSharedMarketUncoveredUrlGroup(recordId, sharedGroupId);
      updateSharedReviewRow(res.data);
      showToast('已加入共享组，待共享运行中重检');
    } catch (err) {
      if (isApiError(err)) showToast(err.message);
      else showToast('操作失败');
    } finally {
      setSharedReviewActionLoadingId(null);
    }
  };

  const sharedReviewPages = Math.ceil(sharedReviewTotal / sharedReviewPageSize) || 0;
  const sharedReviewLabel = (status: string | undefined | null): string => {
    switch (status) {
      case 'pending':
        return '待检测';
      case 'review_required':
        return '待审核';
      case 'matched':
        return '已匹配';
      case 'ignored':
        return '已忽略';
      case 'detecting':
        return '检测中';
      default:
        return status || '未知';
    }
  };

  const totalPages = Math.ceil(total / pageSize);
  const permissionShortcutOperators = operators.filter((op) => op.role !== 'admin');

  return (
    <div className="operators-page">
      <Toast messages={messages} onRemove={removeToast} />
      <div className="operators-header">
        <h1 className="operators-title">操作者管理</h1>
        <button type="button" className="create-btn" onClick={() => setShowCreate(true)}>
          + 创建操作者
        </button>
      </div>

      <section className="shared-review-panel" aria-label="共享网址审核">
        <h2 className="shared-review-title">共享网址审核</h2>
        {sharedReviewLoading ? (
          <p className="loading-text">加载中...</p>
        ) : sharedReviewError ? (
          <p className="operators-error">{sharedReviewError}</p>
        ) : sharedMarketUncoveredRows.length === 0 ? (
          <p className="empty-text">暂无待审核共享网址</p>
        ) : (
          <>
            <div className="shared-review-table-wrap">
              <table className="shared-review-table">
                <thead>
                  <tr>
                    <th>网址</th>
                    <th>平台类型</th>
                    <th>命中次数</th>
                    <th>检测状态</th>
                    <th>失败原因</th>
                    <th>最后发现时间</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {sharedMarketUncoveredRows.map((item) => (
                    <tr key={item.id}>
                      <td data-label="网址" className="mono-url">
                        <span className="shared-review-url">{item.normalized_url}</span>
                      </td>
                      <td data-label="平台类型">{item.last_platform_type || '-'}</td>
                      <td data-label="命中次数">{item.hit_count}</td>
                      <td data-label="检测状态">{sharedReviewLabel(item.detection_status || item.status)}</td>
                      <td data-label="失败原因">{item.failure_reason || '-'}</td>
                      <td data-label="最后发现时间">{item.last_seen_at}</td>
                      <td data-label="操作">
                        <div className="shared-review-actions">
                          <button
                            type="button"
                            className="shared-review-btn shared-review-recheck"
                            onClick={() => handleRecheckSharedReview(item.id)}
                            disabled={sharedReviewActionLoadingId === item.id}
                          >
                            {sharedReviewActionLoadingId === item.id ? '处理中...' : '重新检测'}
                          </button>
                          <button
                            type="button"
                            className="shared-review-btn shared-review-ignore"
                            onClick={() => handleIgnoreSharedReview(item.id)}
                            disabled={sharedReviewActionLoadingId === item.id}
                          >
                            {sharedReviewActionLoadingId === item.id ? '处理中...' : '忽略'}
                          </button>
                          {sharedMarketGroups.length > 0 ? (
                            <div className="shared-review-join">
                              <select
                                value={sharedJoinSelection[item.id] ?? ''}
                                onChange={(e) => {
                                  setSharedJoinSelection((previous) => ({
                                    ...previous,
                                    [item.id]: e.target.value,
                                  }));
                                }}
                                disabled={sharedReviewActionLoadingId === item.id}
                                aria-label="选择共享组"
                              >
                                <option value="">选择共享组</option>
                                {sharedMarketGroups.map((group) => (
                                  <option key={group.id} value={group.id}>
                                    {group.group_key}
                                  </option>
                                ))}
                              </select>
                              <button
                                type="button"
                                className="shared-review-btn shared-review-join-btn"
                                onClick={() => handleJoinSharedGroup(item.id)}
                                disabled={sharedReviewActionLoadingId === item.id || !sharedJoinSelection[item.id]}
                              >
                                {sharedReviewActionLoadingId === item.id ? '处理中...' : '加入共享组'}
                              </button>
                            </div>
                          ) : (
                            <span className="shared-review-no-group">暂无可用共享组</span>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {sharedReviewPages > 1 && (
              <div className="operators-pagination">
                <button
                  type="button"
                  className="page-btn"
                  disabled={sharedReviewPage <= 1}
                  onClick={() => setSharedReviewPage((pageNum) => pageNum - 1)}
                >
                  上一页
                </button>
                <span className="page-info">
                  {sharedReviewPage} / {sharedReviewPages}
                </span>
                <button
                  type="button"
                  className="page-btn"
                  disabled={sharedReviewPage >= sharedReviewPages}
                  onClick={() => setSharedReviewPage((pageNum) => pageNum + 1)}
                >
                  下一页
                </button>
              </div>
            )}
          </>
        )}
      </section>

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

      {!loading && permissionShortcutOperators.length > 0 && (
        <section className="permission-shortcut-panel" aria-label="策略授权快捷入口">
          <div className="permission-shortcut-header">
            <h2>策略授权</h2>
          </div>
          <div className="permission-shortcut-list">
            {permissionShortcutOperators.map((op) => (
              <button
                key={op.id}
                type="button"
                className="permission-shortcut-card"
                onClick={() => openPermissionPanel(op)}
              >
                <span className="permission-shortcut-name">{op.username}</span>
                <span className={`op-status op-status-${op.status}`}>
                  {op.status === 'active' ? '活跃' : op.status === 'disabled' ? '禁用' : op.status}
                </span>
                <span className="permission-shortcut-action">给此操作者授权策略</span>
              </button>
            ))}
          </div>
        </section>
      )}

      {permissionOperator && (
        <div className="permission-panel">
          <div className="permission-panel-header">
            <h3 className="create-form-title">策略授权：{permissionOperator.username}</h3>
            <button type="button" className="cancel-btn" onClick={() => setPermissionOperator(null)}>
              关闭
            </button>
          </div>
          {permissionLoading ? (
            <p className="loading-text">加载中...</p>
          ) : !permissionInfo ? (
            <p className="empty-text">策略授权信息不可用</p>
          ) : (
            <div className="permission-account-list">
              <div className="permission-account">
                <div className="permission-account-title">
                  <strong>{permissionInfo.username}</strong>
                  <span>该操作者名下所有第三方账号共享此授权</span>
                </div>
                <div className="permission-options">
                  {STRATEGY_PERMISSION_OPTIONS.map((option) => (
                    <label key={option.value} className="permission-option">
                      <input
                        type="checkbox"
                        checked={permissionInfo.allowed_strategy_types.includes(option.value)}
                        disabled={savingPermission}
                        onChange={() => handlePermissionToggle(option.value)}
                      />
                      <span>{option.label}</span>
                    </label>
                  ))}
                </div>
              </div>
            </div>
          )}
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
                        <div className="operator-actions">
                          <button
                            type="button"
                            className="permission-btn"
                            onClick={() => openPermissionPanel(op)}
                          >
                            策略授权
                          </button>
                        <button
                          type="button"
                          className={`toggle-btn ${op.status === 'active' ? 'toggle-disable' : 'toggle-enable'}`}
                          onClick={() => handleToggleStatus(op)}
                        >
                          {op.status === 'active' ? '禁用' : '启用'}
                        </button>
                        </div>
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
