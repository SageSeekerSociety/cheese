import { request } from '../http'

/* ---- 平台管理员名单 (`/admin/admins`) ----
 *
 * 「谁算平台管理员」是**服务端**的一个判据（配置里的根名单 ∪ 这张表），客户端只画。
 * 名单分两份给，因为两份在页面上的操作权不一样：`root` 来自部署配置、删不掉，`added`
 * 是页面上加的、每行都有删除按钮。分组规则不在这里再定一份 —— 接口给的就是两块。 */

/** 名单里的一行「这个人是谁」。**两组同一个形状**：`root` 不再是一串裸 handle ——
 *  「这一行是谁」在两组里是同一个问题，两份形状就得让人自己把两处对起来。
 *
 *  两格都可能为 null，而 null 各有各的意思，界面**不许回退**：`nickname` 为 null =
 *  这个人没有 profile 行（或平台上根本没有这个账号），回退成 handle 之后界面就分不清
 *  「没设昵称」和「他叫这个 handle」；`avatar_id` 为 null = 他从没自己挑过头像
 *  （判据在服务端 `UserProfileRepository.chosen_avatar_ids`，不是硬比 id），界面这时
 *  交给 `UserAvatar` 画彩色首字母 —— `getAvatarUrl` 对空值回的空串，不是共用的默认脸。 */
export interface PlatformAdminRow {
  handle: string
  nickname: string | null
  avatar_id: number | null
  /** false = 平台上没有（或已注销）这个账号 —— 这行是死权限，页面要明画，
   *  不许靠 nickname=null 隐式猜。 */
  has_account: boolean
  /** User.created_at；has_account=false 时为 null。 */
  registered_at: string | null
  /** agent 不能做管理动作，所以这行权限用不上 —— 只会从根配置混进来。 */
  is_agent: boolean
}

/** 页面上加进名单的一行：在「这个人是谁」之上多两格出处。`added_by_handle` 是快照：
 *  加人的那个人注销之后，这一行仍然要说得出是谁加的。 */
export interface PlatformAdminAddedRow extends PlatformAdminRow {
  added_by_handle: string
  created_at: string
}

export interface PlatformAdminsPayload {
  /** 部署配置里那份。列得出来，删不掉。 */
  root: PlatformAdminRow[]
  added: PlatformAdminAddedRow[]
}

export function listPlatformAdmins(): Promise<PlatformAdminsPayload> {
  return request<PlatformAdminsPayload>('/admin/admins')
}

/** 「加一个人」那个选择器的候选：按 handle 或昵称搜账号。
 *
 *  单开一条而不是复用用户目录接口：那条只在它取回的那一页里过滤（这个部署上账号
 *  上千，搜昵称十有八九回空），而这里「搜不到」是要么换个说法要么这个人没有账号。
 *  `already_admin` 里的人照常返回 —— 选择器要把他们画成已选中，而不是「搜不到」。 */
export interface AdminCandidate {
  handle: string
  nickname: string
  /** 没挑过头像的人是 null（和反馈卡片、聊天区名册同一条判据），界面画彩色首字母。 */
  avatar_id: number | null
  already_admin: boolean
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
