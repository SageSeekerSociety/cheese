// 后台页面上那些「一份数据既给页面也给组件」的形状放在这里，而不是留在 src/api.ts：
// 组件（src/components/**）不许 import API 层（见 scripts/import-boundary-ratchet-core.mjs），
// 而这类类型只吃 props 的视图也要用。api.ts 反过来从这里 import，和 types/compute.ts、
// types/site.ts 同一条路。

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
