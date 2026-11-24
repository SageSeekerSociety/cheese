// 飞书：团队设的企业自建应用，加上成员各自的授权。
//
// 这一块从 `api.ts` 拆出来。那个文件已经在上限之上（`.claude/scripts/check-file-sizes.py`），
// 只能变短；而飞书这一条线本来就有边界 —— 成员侧走 `/me/integrations/feishu`，团队设置走
// `/teams/{id}/feishu-app`。
//
// 企业自建应用只在它自己那家企业的飞书里能用，所以应用设在团队上：公司的团队设公司的应用，
// 一个人自己用就设在个人团队上。成员的连接只记是哪个团队的应用，授权是本人自己的飞书账号。
// Secret 只写不回显。
import type { Integration } from '../api'

import { request } from '../api'

/* ---- 成员侧 ---- */

/** 本人所在的一个团队，和它设的飞书应用（没设是 `null`）。 */
export interface FeishuTeamChoice {
  team_id: number
  team_name: string
  /** 共享团队的地址名；个人团队是空串（它的设置在个人设置里）。 */
  team_handle: string
  personal: boolean
  /** 本人能不能去设这个团队的应用：个人团队的主人，或团队管理员。 */
  can_manage: boolean
  app: { app_id: string; domain: string } | null
}

export interface FeishuChoices {
  /** 个人团队在前。 */
  teams: FeishuTeamChoice[]
  redirect_uri: string
}

/** 「连接飞书」能用哪些应用。 */
export function feishuChoices(): Promise<FeishuChoices> {
  return request<FeishuChoices>('/me/integrations/feishu')
}

/**
 * 「连接飞书」选好一个团队的应用之后的第一步：先有自己那一行，再跳去飞书授权页。同一个团队
 * 再选一次返回同一行，所以「连接过一半又回来了」不会攒出第二行。
 */
export function connectFeishu(teamId: number): Promise<Integration> {
  return request<Integration>('/me/integrations/feishu', {
    method: 'POST',
    body: JSON.stringify({ team_id: teamId }),
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

/* ---- 团队设置（`/teams/{id}/feishu-app`），能改团队设置的人用 ---- */

export interface TeamFeishuApp {
  configured: boolean
  app_id: string
  domain: string
  updated_by: string
  updated_at: string | null
  /** 要填进应用「重定向 URL」的授权回调地址。 */
  redirect_uri: string
}

export function getTeamFeishuApp(teamId: number): Promise<TeamFeishuApp> {
  return request<TeamFeishuApp>(`/teams/${teamId}/feishu-app`)
}

/** 保存前服务端先拿这套凭据换一次应用令牌，Secret 不对当场报错。`app_secret` 空着表示
 *  不改已经存下的那一个。 */
export function saveTeamFeishuApp(
  teamId: number,
  body: { app_id: string; app_secret: string; domain: string }
): Promise<TeamFeishuApp> {
  return request<TeamFeishuApp>(`/teams/${teamId}/feishu-app`, { method: 'PUT', body: JSON.stringify(body) })
}

export function removeTeamFeishuApp(teamId: number): Promise<TeamFeishuApp> {
  return request<TeamFeishuApp>(`/teams/${teamId}/feishu-app`, { method: 'DELETE' })
}
