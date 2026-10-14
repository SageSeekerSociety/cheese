/**
 * 预览里「谁挑过头像」，从 `proto-feedback-fixtures.ts` 搬出来的一块。
 *
 * 为什么单开一个文件：那一份已经越过 `frontend/src` 的 1000 行尺寸上限
 * （`.claude/scripts/check-file-sizes.py`），越过的文件只许变小 —— 再要往里加东西，
 * 唯一的路就是先搬出去一块（和 `proto-usage-fixtures.ts` 同一个理由）。这一份只被
 * `proto-feedback-fixtures.ts` 引用，进预览构建、不进真实构建，理由同它（见那个文件
 * 顶部的说明）。
 */

/**
 * 「谁挑过头像」——接口的 `author_avatar_id` 在真实现里来自 `UserProfile.avatar_id`，
 * 而且**只装真的挑过的人**：注册时会给人人都写上那张全局默认头像，所以「有 avatar_id」
 * 不等于「挑过」，后端专门有一处把这两种分开（`chosen_avatar_ids`）。
 *
 * 这份假数据照同一件事造：名单里没有的返回 null，界面就画那个按 handle 派生的彩色
 * 首字母。**不要**图省事一律给个 id —— 那正是「所有没挑过头像的人共用同一张脸」，
 * 而区分人正是头像唯一的活。
 */
const CHOSEN_AVATARS: Record<string, number> = {
  andy: 3,
  andylizf: 2,
  chiruotong: 4,
}

export const avatarOf = (handle: string): number | null => CHOSEN_AVATARS[handle] ?? null

/** 一条反馈的指派人那两格：没人指派时是 null（界面据此根本不画头像，也不会把「没这个人」
 *  画成别人）；有人指派时头像是他真挑过的那颗、`assignee_is_agent` 照预览的实情说 false
 *  （预览里领取反馈的都是真人账号）。 */
export function assigneeFace(handle: string | null | undefined) {
  const avatar_id = handle ? avatarOf(handle) : null
  return { assignee_avatar_id: avatar_id, assignee_is_agent: handle ? false : null }
}
