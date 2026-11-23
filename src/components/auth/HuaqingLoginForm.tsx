import { useId, useRef, useState, type FormEvent, type ReactNode } from 'react';

export type LoginCredentials = { username: string; password: string };
type Props = {
  onLogin: (credentials: LoginCredentials) => Promise<unknown>;
  busy?: boolean;
  passwordOnly?: boolean;
  eye: ReactNode;
  eyeOff: ReactNode;
};

/** All service login forms use the same validation, focus and pending behavior. */
export function HuaqingLoginForm({
  onLogin,
  busy = false,
  passwordOnly = false,
  eye,
  eyeOff,
}: Props) {
  const id = useId();
  const usernameRef = useRef<HTMLInputElement>(null);
  const passwordRef = useRef<HTMLInputElement>(null);
  const inFlight = useRef(false);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const [errors, setErrors] = useState({ username: '', password: '' });
  const disabled = busy || pending;
  function change(field: 'username' | 'password', value: string) {
    if (field === 'username') setUsername(value);
    else setPassword(value);
    setError('');
    if (submitted)
      setErrors((previous) => ({
        ...previous,
        [field]: value.trim()
          ? ''
          : field === 'username'
            ? '请输入账号'
            : '请输入密码',
      }));
  }
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (disabled || inFlight.current) return;
    setSubmitted(true);
    setError('');
    const next = {
      username: !passwordOnly && !username.trim() ? '请输入账号' : '',
      password: !password.trim() ? '请输入密码' : '',
    };
    setErrors(next);
    if (next.username || next.password) {
      (next.username ? usernameRef : passwordRef).current?.focus();
      return;
    }
    inFlight.current = true;
    setPending(true);
    try {
      await onLogin({ username: username.trim(), password });
    } catch (cause) {
      const detail = cause as {
        message?: string;
        status?: number;
        response?: { status?: number };
      };
      const status = detail?.status ?? detail?.response?.status;
      const message =
        typeof cause === 'string' ? cause : (detail?.message ?? '');
      setError(
        status === 429 || /次数|too many|rate.?limit/i.test(message)
          ? '尝试次数过多，请稍后重试'
          : (status !== undefined && status >= 500) ||
              /network|fetch|timeout|超时|无法连接|服务.*不可用/i.test(message)
            ? '无法连接服务，请稍后重试'
            : '账号或密码不正确',
      );
    } finally {
      inFlight.current = false;
      setPending(false);
    }
  }
  return (
    <form
      noValidate
      onSubmit={(event) => void submit(event)}
      aria-busy={disabled}
      data-testid="auth-form"
    >
      {!passwordOnly && (
        <div className="hq-auth-field">
          <label htmlFor={`${id}-username`}>账号</label>
          <input
            ref={usernameRef}
            id={`${id}-username`}
            name="username"
            type="text"
            placeholder="请输入账号"
            autoComplete="username"
            required
            value={username}
            onChange={(event) => change('username', event.target.value)}
            disabled={disabled}
            aria-invalid={Boolean(errors.username)}
            aria-describedby={
              errors.username ? `${id}-username-error` : undefined
            }
            data-testid="auth-email"
          />
          {errors.username && (
            <p
              className="hq-auth-field-error"
              id={`${id}-username-error`}
              role="alert"
            >
              {errors.username}
            </p>
          )}
        </div>
      )}
      <div className="hq-auth-field">
        <label htmlFor={`${id}-password`}>密码</label>
        <div className="hq-auth-password">
          <input
            ref={passwordRef}
            id={`${id}-password`}
            name="password"
            type={showPassword ? 'text' : 'password'}
            placeholder="请输入密码"
            autoComplete="current-password"
            required
            value={password}
            onChange={(event) => change('password', event.target.value)}
            disabled={disabled}
            aria-invalid={Boolean(errors.password)}
            aria-describedby={
              errors.password ? `${id}-password-error` : undefined
            }
            data-testid="auth-password"
          />
          <button
            type="button"
            aria-label={showPassword ? '隐藏密码' : '显示密码'}
            aria-pressed={showPassword}
            disabled={disabled}
            onClick={() => setShowPassword((value) => !value)}
          >
            {showPassword ? eyeOff : eye}
          </button>
        </div>
        {errors.password && (
          <p
            className="hq-auth-field-error"
            id={`${id}-password-error`}
            role="alert"
          >
            {errors.password}
          </p>
        )}
      </div>
      {error && (
        <p className="hq-auth-error" role="alert">
          {error}
        </p>
      )}
      <button
        className="hq-auth-submit"
        type="submit"
        disabled={disabled}
        data-testid="auth-submit"
      >
        {disabled ? '正在登录…' : '登录'}
      </button>
    </form>
  );
}
