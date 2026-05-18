import { useState } from "react";
import type { WeReadBinding, WeReadQrLoginSession } from "../types";
import {
  isQrInProgress,
  formatStatusLabel,
  formatStatusTone,
} from "../hooks/useWeReadBinding";

function formatTimestamp(value: string | null): string {
  if (!value) return "";
  return new Date(value).toLocaleString("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

type Props = {
  wereadBinding: WeReadBinding | null;
  bindingError: string;
  qrLoginSession: WeReadQrLoginSession | null;
  isStartingQrLogin: boolean;
  isValidatingBinding: boolean;
  onStartQrLogin: () => void;
  onCancelQrLogin: () => void;
  onValidateBinding: () => void;
  onClearBinding: () => void;
};

export function WeReadBindingCard({
  wereadBinding,
  bindingError,
  qrLoginSession,
  isStartingQrLogin,
  isValidatingBinding,
  onStartQrLogin,
  onCancelQrLogin,
  onValidateBinding,
  onClearBinding,
}: Props) {
  const [expanded, setExpanded] = useState(false);

  const statusTone = formatStatusTone(wereadBinding, qrLoginSession);
  const statusLabel = formatStatusLabel(wereadBinding, qrLoginSession);

  return (
    <section className="binding-card">
      <button
        className={`binding-card-header ${expanded ? "is-expanded" : ""}`}
        onClick={() => setExpanded((v) => !v)}
        type="button"
      >
        <div>
          <div className="sidebar-kicker">微信读书</div>
          <h3>微信读书</h3>
        </div>
        <span className={`status-tag is-${statusTone}`}>
          {statusLabel}
        </span>
        <span className="binding-collapse-arrow">{expanded ? "▾" : "▸"}</span>
      </button>
      {expanded && (
        <>
          <p className="binding-copy">
            扫码后 cookie 会由后端 Playwright 浏览器抓取并加密保存，前端不会接触微信读书凭证。
          </p>
          {bindingError ? <div className="binding-error">{bindingError}</div> : null}
          {!bindingError && wereadBinding?.last_error ? (
            <div className="binding-error">{wereadBinding.last_error}</div>
          ) : null}
          {isQrInProgress(qrLoginSession) && qrLoginSession?.qr_image_base64 ? (
            <div className="binding-qr">
              <img
                alt="微信读书扫码登录二维码"
                src={`data:image/png;base64,${qrLoginSession.qr_image_base64}`}
              />
              <span>请在手机微信中扫码登录微信读书</span>
            </div>
          ) : null}
          {isQrInProgress(qrLoginSession) && !qrLoginSession?.qr_image_base64 ? (
            <div className="binding-note">正在准备二维码，请稍候...</div>
          ) : null}
          {wereadBinding?.connected && wereadBinding.updated_at ? (
            <div className="binding-note">最近更新：{formatTimestamp(wereadBinding.updated_at)}</div>
          ) : null}
          {wereadBinding?.connected && wereadBinding.last_validated_at ? (
            <div className="binding-note">上次校验：{formatTimestamp(wereadBinding.last_validated_at)}</div>
          ) : null}
          <div className="binding-actions">
            {qrLoginSession &&
              (qrLoginSession.status === "pending" || qrLoginSession.status === "qr_ready") ? (
              <button className="ghost-button" onClick={onCancelQrLogin} type="button">
                取消扫码
              </button>
            ) : (
              <button
                className="ghost-button"
                onClick={onStartQrLogin}
                type="button"
                disabled={isStartingQrLogin || isValidatingBinding}
              >
                {isStartingQrLogin ? "启动中..." : wereadBinding?.connected ? "重新连接" : "扫码连接"}
              </button>
            )}
            {wereadBinding?.connected && !isQrInProgress(qrLoginSession) ? (
              <button
                className="ghost-button"
                onClick={onValidateBinding}
                type="button"
                disabled={isValidatingBinding}
              >
                {isValidatingBinding ? "检查中..." : "检查连接"}
              </button>
            ) : null}
            {wereadBinding?.connected ? (
              <button className="ghost-button" onClick={onClearBinding} type="button">
                解除绑定
              </button>
            ) : null}
          </div>
        </>
      )}
    </section>
  );
}
