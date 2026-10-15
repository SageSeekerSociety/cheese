/**
 * 分支保护 (#718) 的形状：平台侧的合并规则，照 GitHub 分支保护那一页配置。
 * GitHub 能判定的听 GitHub，判定不了的平台按这份配置补位。
 *
 * 从 `cx_types.ts` 里的「一个形状一页」那个文件挪出来，成自己一处：它是一组
 * 只在这个设置区用的形状 —— 渲染那一件、算可选名单的 composable、读写的 api
 * 都从这里取，`cx_types.ts` 里别的形状和它互不依赖。
 */
export interface RequiredCheck {
  name: string
  // 相对仓库根的 glob；缺省/为空 = 每个 PR 都要求这条检查。
  paths?: string[]
}
// 可写的规则本体。PUT partial-update：body 里出现哪个键就改哪个，返回值也是这一块。
export interface BranchProtectionRules {
  required_checks: RequiredCheck[]
  strict: boolean
  dismiss_stale: boolean
  auto_merge_allowed: boolean
  // null = 未配置：默认由项目 owner 和 lead 放行。
  override_handles: string[] | null
  approvals_required: number
  // '' = 未指定。
  default_reviewer: string
}
export type BranchProtectionPatch = Partial<BranchProtectionRules>
// GET 额外带两块只读附注，说明 GitHub 那一侧的现实。
export interface BranchProtection extends BranchProtectionRules {
  // 绑定 GitHub 的项目从仓库设置读；未绑定固定 'squash'。只读。
  merge_method: string
  github_protection: {
    enforced: boolean
    status: 'unbound' | 'enforced' | 'none' | 'unknown'
    detail?: string | null
  }
}
