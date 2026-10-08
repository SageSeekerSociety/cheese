/**
 * 设置页那六件（`components/settings/*.vue`）在预览站里吃的数据。
 *
 * 形状不是编的：就是那六件自己声明的 props，少一个键多一个键都在 `vue-tsc` 那里
 * 当场红（`bpRules` / `accountConn` 的返回类型引的是 `@/cx_types` 那两个接口）。
 * 值照抄 `views/ProjectSettingsView.vue` 那一层真实的
 * 传法 —— 成员名单来自项目成员、`attributionItems` 的三档文案来自
 * `composables/useProjectSettings.ts`（`跟随系统默认（开启/关闭）` 那一档里带着部署的
 * 默认值）、`merge_method` / `github_protection` 是 `GET /projects/{id}/branch-protection`
 * 带回来那两块只读附注。
 *
 * 为什么单独一份文件：`catalogFixtures.ts` 已经八百多行、`catalog.ts` 也八百多行，
 * 六件两三格的数据塞进去会顶到 `frontend/src` 那一千行的上限；和 `catalogQueueFixtures.ts`
 * 同一个理由。条目本身在 `catalogSettings.ts`，那边的 `CatalogEntry` 是 type-only 引用。
 *
 * 为什么这六件能在预览站里单独画：它们都是「只吃 props、只往上发事件」的那种组件
 * （`frontend_grade.py` 的 A 级），取数全在 `composables/useProjectSettings.ts` 和
 * `composables/useBranchProtection.ts` 里。
 */
import type { BranchProtectionLoadState } from '@/composables/useBranchProtection'
import type { CallbackNotice, GithubAccountLoadState } from '@/composables/useProjectSettings'
import type { BranchProtection, ForgeConnection, OAuthConnectionInfo } from '@/cx_types'

/** 一条规则本体。`github_protection` 默认是「没绑 GitHub / 查得到、没开保护」。 */
export function bpRules(over: Partial<BranchProtection> = {}): BranchProtection {
  return {
    required_checks: [],
    strict: true,
    dismiss_stale: true,
    auto_merge_allowed: false,
    override_handles: null,
    approvals_required: 1,
    default_reviewer: '',
    merge_method: 'squash',
    github_protection: { enforced: false, status: 'none' },
    ...over,
  }
}

/** 人工放行名单的可选项（成员，去掉 AI 队友）。 */
export const BP_MEMBERS = [
  { title: '爱丽丝', value: 'alice' },
  { title: '老王', value: 'wang' },
]

/** 任务默认 reviewer 的可选项：多一档「未指定」。 */
export const BP_REVIEWERS = [{ title: '未指定', value: '' }, ...BP_MEMBERS]

/** 分支保护那一块吃的十样。 */
export function bpProps(
  over: {
    state?: BranchProtectionLoadState
    loadError?: string | null
    bp?: BranchProtection | null
    saving?: string | null
    error?: string | null
    checkName?: string
    checkPaths?: string
    approvalsDraft?: string
  } = {}
) {
  return {
    state: 'loaded' as BranchProtectionLoadState,
    loadError: null as string | null,
    bp: bpRules(),
    saving: null as string | null,
    error: null as string | null,
    memberItems: BP_MEMBERS,
    reviewerItems: BP_REVIEWERS,
    checkName: '',
    checkPaths: '',
    approvalsDraft: '1',
    ...over,
  }
}

/** 一份仓库状态。默认是「GitHub App 项目，还没接上仓库」。 */
export function forgeConn(over: Partial<ForgeConnection> = {}): ForgeConnection {
  return { kind: 'github_app', connected: false, repo: null, url: null, ...over }
}

/** 一条流程结果。仓库那条和账号那条各传各的。 */
export function callbackNotice(over: Partial<CallbackNotice> = {}): CallbackNotice {
  return { type: 'success', text: '已连接 GitHub 仓库。', ...over }
}

/** 署名三档；`跟随系统默认` 那一档的名字里带着部署的默认值，和页面算的一样。 */
export const ATTRIBUTION_ITEMS = [
  { title: '跟随系统默认（开启）', value: 'default' },
  { title: '开启', value: 'on' },
  { title: '关闭', value: 'off' },
]

/** 一个绑着的 GitHub 账号。`tokenExpires: null` = user token 不过期（不等于已过期）。 */
export function accountConn(over: Partial<OAuthConnectionInfo> = {}): OAuthConnectionInfo {
  return {
    id: 1,
    providerId: 'github_app',
    providerName: 'GitHub',
    providerUserId: '583231',
    connectedAt: '2026-09-01T00:00:00Z',
    login: 'octocat',
    tokenExpires: null,
    hasRefreshToken: true,
    ...over,
  }
}

/** 连接 GitHub 账号那一块吃的六样。 */
export function accountProps(
  over: {
    state?: GithubAccountLoadState
    loadError?: string | null
    conn?: OAuthConnectionInfo | null
    connecting?: boolean
    disconnecting?: boolean
    notice?: CallbackNotice | null
  } = {}
) {
  return {
    state: 'loaded' as GithubAccountLoadState,
    loadError: null as string | null,
    conn: null as OAuthConnectionInfo | null,
    connecting: false,
    disconnecting: false,
    notice: null as CallbackNotice | null,
    ...over,
  }
}

/** 「归档项目」那一块和它的弹窗：项目名取自模型管理那一组的夹具里的一个真项目。 */
export const ARCHIVE_PROJECT = '课程资料整理'

/** 被拒时弹窗里那句：后端 `archiveOwnerOnly` 的原话（`apiError.json`），composable 原样递下来。 */
export const ARCHIVE_REFUSED = '只有项目所有者能归档或取消归档项目'
