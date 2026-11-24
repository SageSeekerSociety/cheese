// 飞书：成员带自己公司的企业自建应用来，授权自己的账号。
//
// 这一块从 `api.ts` 拆出来。那个文件已经在上限之上（`.claude/scripts/check-file-sizes.py`），
// 只能变短；而飞书这一条线本来就有边界 —— 成员侧都走 `/me/integrations/feishu`。
//
// 企业自建应用只在它自己那家企业的飞书里能用，所以平台不持有飞书应用：每个连接带着成员
// 自己公司的 App ID / Secret（`connectFeishu`）。Secret 只写不回显。
import type { Integration } from '../api'

import { request } from '../api'

/** 成员建自己公司的应用时要用到的东西：要填进那个应用「重定向 URL」的授权回调地址。 */
export interface FeishuSetup {
  redirect_uri: string
}

export function feishuSetup(): Promise<FeishuSetup> {
  return request<FeishuSetup>('/me/integrations/feishu')
}

/** 成员自己公司的企业自建应用。`domain` 是 `feishu`（飞书）或 `lark`。 */
export interface FeishuOwnApp {
  app_id: string
  app_secret: string
  domain: string
}

/**
 * 「连接飞书」按下的第一步：先有自己那一行，再跳去飞书授权页。
 *
 * 服务端先拿这套凭据换一次应用令牌，Secret 填错当场就说。同一个 App ID 再提交一次是改
 * 那一行，所以「连接过一半又回来了」不会攒出第二行。
 */
export function connectFeishu(app: FeishuOwnApp): Promise<Integration> {
  return request<Integration>('/me/integrations/feishu', {
    method: 'POST',
    body: JSON.stringify(app),
  })
}

/** 某一行的授权地址。回调要靠行 id 认出是谁授权的，所以这一定是已经存在的那一行。 */
export function feishuAuthorizeUrl(id: string): Promise<{ url: string; redirect_uri: string }> {
  return request<{ url: string; redirect_uri: string }>(`/me/integrations/${encodeURIComponent(id)}/feishu/authorize`)
}

/** 从飞书回来时地址栏里的 `feishu_handoff`：回调没存任何东西，只把结果替发起人留着。
 *  登录着的这一页去领（改这一行连接：`feishu_handoff`），只有这行连接的主人领得到，领到
 *  才存。回的 `authorization` 是这一次授权的结果（`feishu`，可能还有 `feishu_detail`）。 */
export function claimFeishuHandoff(
  integrationId: string,
  handoff: string
): Promise<{ authorization: Record<string, string> }> {
  return request<{ authorization: Record<string, string> }>(`/me/integrations/${encodeURIComponent(integrationId)}`, {
    method: 'PATCH',
    body: JSON.stringify({ feishu_handoff: handoff }),
  })
}
