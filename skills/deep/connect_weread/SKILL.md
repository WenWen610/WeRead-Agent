---
name: connect-weread
description: 用于在 WeRead 工具返回 binding_required 或 reauth_required 时自主调用 connect_weread 让用户扫码绑定。Use this when a WeRead tool requires binding or reauthentication.
---

# 连接微信读书

## 使用场景

当任意 WeRead 工具返回 `status: "binding_required"` 或 `status: "reauth_required"` 时触发。

## 工作流

1. 收到 `binding_required` 或 `reauth_required` 后，**立即自主调用 `connect_weread`**，不要重试其他 WeRead 工具。
2. `connect_weread` 返回 QR 码后，告知用户扫描二维码绑定微信读书。
3. 如果用户再次提出原始请求，重新尝试 WeRead 工具。

## 规则

- 系统不会中断 agent 循环：capability middleware 只标记状态，由你自主决定调用 `connect_weread`。
- 不要假装绑定已成功；等待用户告知扫描完成后再重试 WeRead 工具。
- 绑定完成后，`pending_capability_requirement` 会被自动清除。
