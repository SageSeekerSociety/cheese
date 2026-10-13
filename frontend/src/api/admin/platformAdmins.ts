import type { AdminCandidate, PlatformAdminsPayload } from '@/types'

import { request } from '../http'

/* ---- 平台管理员名单 (`/admin/admins`) ----
 *
 * 「谁算平台管理员」是**服务端**的一个判据（配置里的根名单 ∪ 这张表），客户端只画。
 * 名单分两份给，因为两份在页面上的操作权不一样：`root` 来自部署配置、删不掉，`added`
 * 是页面上加的、每行都有删除按钮。分组规则不在这里再定一份 —— 接口给的就是两块。 */

// 「成员管理」那一屏拆出来的半张画面只认 props、不连网络层，不能从这儿取类型。名单的
// 形状（行 / 已加行 / 整份 / 候选）挪到领域类型 `@/types/admin.ts`，这里原样转出去，
// 对外的 import 路径不变。
export type { AdminCandidate, PlatformAdminAddedRow, PlatformAdminRow, PlatformAdminsPayload } from '@/types'

export function listPlatformAdmins(): Promise<PlatformAdminsPayload> {
  return request<PlatformAdminsPayload>('/admin/admins')
}

export function searchAdminCandidates(q: string, limit = 20): Promise<{ items: AdminCandidate[] }> {
  return request<{ items: AdminCandidate[] }>(
    `/admin/users?q=${encodeURIComponent(q)}&limit=${encodeURIComponent(String(limit))}`
  )
}

/** 加一个人。回的是**更新后的整份名单**（`created` 说明这次是真加了还是他本来就在）：
 *  加完之后页面上两块都可能变，让客户端自己再拉一次中间那一下页面是旧的。 */
export function addPlatformAdmin(handle: string): Promise<PlatformAdminsPayload & { created: boolean }> {
  return request<PlatformAdminsPayload & { created: boolean }>('/admin/admins', {
    method: 'POST',
    body: JSON.stringify({ handle }),
  })
}

/** 从名单里移出一个人，同样回整份（`removed` 说这次有没有真删掉一行 —— 删一个不在
 *  名单里的人不是错误，他的目的已经成立了）。根管理员到这里会拿到 409。 */
export function removePlatformAdmin(handle: string): Promise<PlatformAdminsPayload & { removed: boolean }> {
  return request<PlatformAdminsPayload & { removed: boolean }>(`/admin/admins/${encodeURIComponent(handle)}`, {
    method: 'DELETE',
  })
}
