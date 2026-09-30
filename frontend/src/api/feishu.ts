// 飞书：平台管理员配过一次的那个应用，加上成员各自的授权。
//
// 这一块从 `api.ts` 拆出来。那个文件已经在上限之上（`.claude/scripts/check-file-sizes.py`），
// 只能变短；而飞书这一条线本来就有边界 —— 成员侧走 `/me/integrations/feishu`，管理端走
// `/admin/integrations/feishu`，两边共用同一份应用凭据 —— 所以拆在这里是一个有理由的整体。
//
// 凭据是组织一份、账号是个人一份：应用由管理员在「飞书应用」那页填一次，成员点一下
// 「连接飞书」出自己的授权。所以这里的 secret 只写不回显，`connectFeishu` 也不带正文。
import type { Integration } from '../api'

import { request } from '../api'

/* ---- 成员侧 ---- */

/**
 * 平台配过飞书应用没有 —— 「连接飞书」那颗按钮亮不亮就是这个问题的答案。
 *
 * 没有它的时候按钮是灰的，而灰的原因只有一个：管理员还没填那一次（`/admin/integrations/feishu`）。
 */
export interface FeishuAvailability {
  configured: boolean
  app_id: string
  domain: string
}

export function feishuAvailability(): Promise<FeishuAvailability> {
  return request<FeishuAvailability>('/me/integrations/feishu')
}

/**
 * 「连接飞书」按下的第一步：先有自己那一行，再跳去飞书授权页。
 *
 * 不带任何正文 —— 应用凭据是平台管理员的，成员这边只出自己的账号。已有的那一行会被
 * 原样返回（这个动作是幂等的），所以「连接过一半又回来了」不会攒出第二行。
 */
export function connectFeishu(): Promise<Integration> {
  return request<Integration>('/me/integrations/feishu', { method: 'POST' })
}

/** 某一行的授权地址。回调要靠行 id 认出是谁授权的，所以这一定是已经存在的那一行。 */
export function feishuAuthorizeUrl(id: string): Promise<{ url: string; redirect_uri: string }> {
  return request<{ url: string; redirect_uri: string }>(`/me/integrations/${encodeURIComponent(id)}/feishu/authorize`)
}

/* ---- 管理端（`/admin/integrations`）----
 *
 * 平台上**唯一**一处飞书应用凭据（`backend/app/api/routes/admin_integrations.py`）。
 * 成员不再各自去飞书建一个应用，所以这一页是管理员填一次、所有人共用的那一次。
 *
 * Secret 只写不回显：读回来的结构里根本没有它，所以 `save` 的空串表示「不改已经存下的
 * 那一个」，不是「清空」—— 改了域名或 App ID 不必先把它找回来。
 */

export interface PlatformFeishuApp {
  configured: boolean
  app_id: string
  domain: string
  updated_by: string
  updated_at: string | null
}

export function getPlatformFeishuApp(): Promise<PlatformFeishuApp> {
  return request<PlatformFeishuApp>('/admin/integrations/feishu')
}

export function savePlatformFeishuApp(body: {
  app_id: string
  app_secret: string
  domain: string
}): Promise<PlatformFeishuApp> {
  return request<PlatformFeishuApp>('/admin/integrations/feishu', {
    method: 'PUT',
    body: JSON.stringify(body),
  })
}
