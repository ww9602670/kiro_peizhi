import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { fetchOperatorMe, updateOperatorPassword } from '@/api/auth';
import { isApiError } from '@/api/request';
import type { OperatorMeInfo } from '@/types/api/operator';
import './My.css';

const SUCCESS_MESSAGE_TIMEOUT_MS = 3000;

export default function My() {
  const [data, setData] = useState<OperatorMeInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [oldPassword, setOldPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [submitError, setSubmitError] = useState('');
  const [submitSuccess, setSubmitSuccess] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const res = await fetchOperatorMe();
      setData(res.data ?? null);
    } catch (err) {
      setError(isApiError(err) ? err.message : '加载我的页面失败，请稍后再试。');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (!submitSuccess) {
      return undefined;
    }

    const timer = window.setTimeout(() => setSubmitSuccess(''), SUCCESS_MESSAGE_TIMEOUT_MS);
    return () => window.clearTimeout(timer);
  }, [submitSuccess]);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSubmitError('');
    setSubmitSuccess('');

    if (!oldPassword.trim() || !newPassword.trim() || !confirmPassword.trim()) {
      setSubmitError('请完整填写当前密码、新密码和确认密码。');
      return;
    }

    if (newPassword !== confirmPassword) {
      setSubmitError('两次输入的新密码不一致。');
      return;
    }

    setSubmitting(true);
    try {
      await updateOperatorPassword({
        old_password: oldPassword,
        new_password: newPassword,
      });
      setOldPassword('');
      setNewPassword('');
      setConfirmPassword('');
      setSubmitSuccess('密码修改成功。');
    } catch (err) {
      setSubmitError(isApiError(err) ? err.message : '修改密码失败，请稍后再试。');
    } finally {
      setSubmitting(false);
    }
  };

  if (loading && !data) {
    return <div className="my-page"><p className="my-state">加载中...</p></div>;
  }

  if (error && !data) {
    return <div className="my-page"><p className="my-state my-state-error">{error}</p></div>;
  }

  if (!data) {
    return null;
  }

  return (
    <div className="my-page">
      <header className="my-header">
        <div>
          <h1 className="my-title">我的</h1>
          <p className="my-subtitle">查看运营账号配额与有效期，并修改当前登录密码。</p>
        </div>
        <button type="button" className="my-refresh" onClick={() => void load()}>
          刷新
        </button>
      </header>

      <section className="my-summary-grid">
        <article className="my-card">
          <span className="my-card-label">当前账号</span>
          <strong className="my-card-value">{data.username}</strong>
        </article>
        <article className="my-card">
          <span className="my-card-label">到期时间</span>
          <strong className="my-card-value">{data.expire_date ?? '未设置'}</strong>
        </article>
        <article className="my-card">
          <span className="my-card-label">可绑总量</span>
          <strong className="my-card-value">{data.max_accounts}</strong>
        </article>
        <article className="my-card">
          <span className="my-card-label">剩余额度</span>
          <strong className="my-card-value">{data.remaining_accounts}</strong>
        </article>
      </section>

      <section className="my-panel">
        <h2 className="my-panel-title">额度情况</h2>
        <div className="quota-row">
          <span>已绑定账号</span>
          <strong>{data.bound_accounts}</strong>
        </div>
        <div className="quota-row">
          <span>剩余可绑</span>
          <strong>{data.remaining_accounts}</strong>
        </div>
      </section>

      <section className="my-panel">
        <h2 className="my-panel-title">修改登录密码</h2>
        <form className="my-form" onSubmit={handleSubmit}>
          <label className="my-field">
            <span className="my-field-label">当前密码</span>
            <input
              type="password"
              className="my-input"
              value={oldPassword}
              onChange={(event) => setOldPassword(event.target.value)}
              autoComplete="current-password"
              disabled={submitting}
            />
          </label>
          <label className="my-field">
            <span className="my-field-label">新密码</span>
            <input
              type="password"
              className="my-input"
              value={newPassword}
              onChange={(event) => setNewPassword(event.target.value)}
              autoComplete="new-password"
              disabled={submitting}
            />
          </label>
          <label className="my-field">
            <span className="my-field-label">确认新密码</span>
            <input
              type="password"
              className="my-input"
              value={confirmPassword}
              onChange={(event) => setConfirmPassword(event.target.value)}
              autoComplete="new-password"
              disabled={submitting}
            />
          </label>

          {submitError && <p className="my-message my-message-error" role="alert">{submitError}</p>}
          {submitSuccess && <p className="my-message my-message-success">{submitSuccess}</p>}

          <button type="submit" className="my-submit" disabled={submitting}>
            {submitting ? '提交中...' : '确认修改'}
          </button>
        </form>
      </section>
    </div>
  );
}
