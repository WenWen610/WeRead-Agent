import type { AuthState } from "../hooks/useAuth";

type Props = {
  authState: AuthState | null;
  email: string;
  password: string;
  authMode: "login" | "register";
  authError: string;
  onChangeEmail: (v: string) => void;
  onChangePassword: (v: string) => void;
  onChangeAuthMode: (v: "login" | "register") => void;
  onSubmit: () => void;
};

export function AuthPanel({
  authState,
  email,
  password,
  authMode,
  authError,
  onChangeEmail,
  onChangePassword,
  onChangeAuthMode,
  onSubmit,
}: Props) {
  if (authState) return null;

  return (
    <div className="auth-shell">
      <div className="auth-card">
        <div className="auth-badge">WeRead Agent</div>
        <h1>接上你的微信读书对话界面</h1>
        <p>
          先登录，再创建应用内 session。后端已经接好 thread、timeline 和
          clarification，你现在只需要把前端跑起来。
        </p>
        <div className="auth-tabs">
          <button
            className={authMode === "login" ? "is-active" : ""}
            onClick={() => onChangeAuthMode("login")}
            type="button"
          >
            登录
          </button>
          <button
            className={authMode === "register" ? "is-active" : ""}
            onClick={() => onChangeAuthMode("register")}
            type="button"
          >
            注册
          </button>
        </div>
        <label>
          邮箱
          <input
            value={email}
            onChange={(e) => onChangeEmail(e.target.value)}
            type="email"
          />
        </label>
        <label>
          密码
          <input
            value={password}
            onChange={(e) => onChangePassword(e.target.value)}
            type="password"
          />
          {authMode === "register" ? (
            <span className="auth-hint">至少 8 位即可，无需大小写、数字或符号</span>
          ) : null}
        </label>
        {authError ? <div className="auth-error">{authError}</div> : null}
        <button className="auth-submit" onClick={onSubmit} type="button">
          {authMode === "login" ? "登录并创建会话" : "注册并创建会话"}
        </button>
      </div>
    </div>
  );
}
