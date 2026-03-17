/**
 * 登录页面（Mobile-First）
 * - 用户名 + 密码表单
 * - 登录成功后跳转首页
 * - 错误提示
 */

import { type FormEvent, useState } from 'react';
import { useAuth } from '@/hooks/useAuth';
import { isApiError } from '@/api/request';
import './Login.css';

export default function Login() {
  const { login, isAuthenticated } = useAuth();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  // 已登录 → 不渲染登录表单（App.tsx 会根据 isAuthenticated 切换页面）
  if (isAuthenticated) {
    return null;
  }

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError('');

    if (!username.trim() || !password.trim()) {
      setError('请输入用户名和密码');
      return;
    }

    setLoading(true);
    try {
      await login({ username: username.trim(), password });
    } catch (err) {
      if (isApiError(err)) {
        setError(err.message);
      } else {
        setError('登录失败，请稍后重试');
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="login-page">
      <form onSubmit={handleSubmit} className="login-form">
        <h1 className="login-title">登录</h1>

        {error && (
          <div role="alert" className="login-error">
            {error}
          </div>
        )}

        <div className="login-field">
          <label htmlFor="username" className="login-label">
            用户名
          </label>
          <input
            id="username"
            type="text"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
            disabled={loading}
            className="login-input"
            placeholder="请输入用户名"
          />
        </div>

        <div className="login-field">
          <label htmlFor="password" className="login-label">
            密码
          </label>
          <input
            id="password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            disabled={loading}
            className="login-input"
            placeholder="请输入密码"
          />
        </div>

        <button
          type="submit"
          disabled={loading}
          className="login-button"
        >
          {loading ? '登录中...' : '登录'}
        </button>
      </form>
    </div>
  );
}
