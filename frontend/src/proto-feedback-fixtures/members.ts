/**
 * proto-feedback-fixtures/members.ts — 成员管理（`/admin/members`）那份假名单。
 *
 * 页面上加的人真的进这里（`ADDED_ADMINS`），看板上的 `admins` 数跟着变 —— 「看板说 9
 * 个、成员管理页列出来 8 个」的那种对不上就是从这儿来的。
 */
import { ago } from './support'

/* ---- 成员管理（`/admin/members`）的假数据 ---- */

/** 部署配置里那份根名单。页面上**删不掉**，所以它是一份常量，不受 POST/DELETE 影响
 *  —— 预览里那几行没有「移出」按钮，正是要看的那个形态。 */
export const ROOT_ADMINS = ['andy', 'wangchangxin']

/** 页面上加的那一份。形状照服务端的两块（`root` / `added`）分开给，不合成一个带标记
 *  的数组：两块的**操作权不一样**，分组规则不该由前端再定一份。 */
export const ADDED_ADMINS: { handle: string; added_by_handle: string; created_at: string }[] = [
  { handle: 'pengwenbo', added_by_handle: 'andy', created_at: ago(60 * 24 * 3) },
]

/** 加人那个搜索框里的账号池。真环境是 1199 个账号、搜索在 SQL 里；预览里这几个人
 *  够点出「搜 handle 或昵称都能搜到」「已经是管理员的置灰」这两种形态。 */
export const ACCOUNTS: { handle: string; nickname: string }[] = [
  { handle: 'andy', nickname: 'andy' },
  { handle: 'andylizf', nickname: 'andylizf' },
  { handle: 'caisongyang', nickname: '蔡松洋' },
  { handle: 'chiruotong', nickname: '池若彤' },
  { handle: 'ligan', nickname: '李甘' },
  { handle: 'maxiaoyu', nickname: '马霄宇' },
  { handle: 'n1ctheboy', nickname: '李甘-nictheboy' },
  { handle: 'pengwenbo', nickname: '彭文博' },
  { handle: 'wangchangxin', nickname: '符露夀' },
]

export function adminRoster() {
  return { root: [...ROOT_ADMINS], added: ADDED_ADMINS.map((row) => ({ ...row })) }
}

export function isAdminHandle(handle: string): boolean {
  return ROOT_ADMINS.includes(handle) || ADDED_ADMINS.some((row) => row.handle === handle)
}
