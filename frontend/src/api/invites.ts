// 邀请码：每个人自己的那一张（`/users/me/invites`），和平台管理员发的码（`/users/invite-codes`）。
import type { AdminInviteCode, MyInvites } from '@/types/invites'

import { request } from './http'

/** 自己的邀请码、余量、奖励和邀请来的人；第一次读时才建码。 */
export const getMyInvites = () => request<MyInvites>('/users/me/invites')

export const listInviteCodes = () => request<{ codes: AdminInviteCode[] }>('/users/invite-codes')

export const createInviteCode = (maxUses: number, note?: string) =>
  request<{ code: string; id: number; maxUses: number }>('/users/invite-codes', {
    method: 'POST',
    body: JSON.stringify({ maxUses, note: note || undefined }),
  })

/** 停用：这个码不能再用来注册。 */
export const deactivateInviteCode = (id: number) => request<null>(`/users/invite-codes/${id}`, { method: 'DELETE' })

/** 带着邀请码的注册链接：打开即填好邀请码。 */
export const inviteSignupLink = (code: string) =>
  `${window.location.origin}/account/signup?code=${encodeURIComponent(code)}`
