/**
 * 后台「成员管理」那几屏的形状（`/admin/members`）。
 *
 * 这几个原来长在 `api/admin/platformAdmins.ts` 里；挪到领域类型这儿，是因为这一屏拆出来
 * 的那半张画面（`views/admin/AdminMembersPageView.vue`）只认 props、不连网络层，不能从
 * `@/api` 取类型。网络层再从这儿转出去，对外的 import 路径一个字没变。
 */

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
