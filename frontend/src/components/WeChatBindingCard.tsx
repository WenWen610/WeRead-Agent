import { useState } from "react";
import type { ChannelStatus, WeixinLoginResponse } from "../api";

type Props = {
  channel: ChannelStatus | null;
  error: string;
  qrData: WeixinLoginResponse | null;
  isGeneratingQr: boolean;
  isPolling: boolean;
  onStartQrLogin: () => void;
  onCancelQrLogin: () => void;
  onStartChannel: () => void;
  onStopChannel: () => void;
};

function statusLabel(channel: ChannelStatus | null, isPolling: boolean): string {
  if (isPolling) return "等待扫码...";
  if (!channel || !channel.has_token) return "未配置";
  if (channel.user_bound && channel.running) return "运行中";
  if (channel.user_bound) return "已绑定（未运行）";
  return "未连接";
}

function statusTone(channel: ChannelStatus | null, isPolling: boolean): string {
  if (isPolling) return "warning";
  if (!channel || !channel.has_token) return "muted";
  if (channel.user_bound && channel.running) return "active";
  if (channel.user_bound) return "warning";
  return "muted";
}

export function WeChatBindingCard({
  channel,
  error,
  qrData,
  isGeneratingQr,
  isPolling,
  onStartQrLogin,
  onCancelQrLogin,
  onStartChannel,
  onStopChannel,
}: Props) {
  const [expanded, setExpanded] = useState(false);

  const tone = statusTone(channel, isPolling);
  const label = statusLabel(channel, isPolling);

  return (
    <section className="binding-card">
      <button
        className={`binding-card-header ${expanded ? "is-expanded" : ""}`}
        onClick={() => setExpanded((v) => !v)}
        type="button"
      >
        <div>
          <div className="sidebar-kicker">微信 Bot</div>
          <h3>微信端</h3>
        </div>
        <span className={`status-tag is-${tone}`}>
          {label}
        </span>
        <span className="binding-collapse-arrow">{expanded ? "▾" : "▸"}</span>
      </button>
      {expanded && (
        <>
          <p className="binding-copy">
            绑定你的个人微信号作为 AI 对话渠道。扫描二维码即可完成授权。
          </p>
          {error ? <div className="binding-error">{error}</div> : null}
          {qrData?.qrcode_img_content ? (
            <div className="binding-qr">
              <img
                alt="微信扫码授权 Bot"
                src={`data:image/png;base64,${qrData.qrcode_img_content}`}
              />
              <span>请在微信中扫码登录</span>
            </div>
          ) : null}
          {isPolling && !qrData?.qrcode_img_content ? (
            <div className="binding-note">正在准备二维码，请稍候...</div>
          ) : null}
          {channel?.has_token && channel?.running ? (
            <div className="binding-note">Bot 已连接，可通过微信发送消息与 AI 对话。</div>
          ) : null}
          {channel?.user_bound && !channel?.running ? (
            <div className="binding-note">已绑定微信，启动 Bot 后即可对话。</div>
          ) : null}
          <div className="binding-actions">
            {isPolling || qrData ? (
              <button className="ghost-button" onClick={onCancelQrLogin} type="button">
                取消扫码
              </button>
            ) : (
              <button
                className="ghost-button"
                onClick={onStartQrLogin}
                type="button"
                disabled={isGeneratingQr}
              >
                {isGeneratingQr ? "生成中..." : channel?.user_bound ? "重新授权" : "扫码连接"}
              </button>
            )}
            {channel?.has_token && !channel?.running ? (
              <button className="ghost-button" onClick={onStartChannel} type="button">
                启动 Bot
              </button>
            ) : null}
            {channel?.running ? (
              <button className="ghost-button" onClick={onStopChannel} type="button">
                停止 Bot
              </button>
            ) : null}
          </div>
        </>
      )}
    </section>
  );
}
