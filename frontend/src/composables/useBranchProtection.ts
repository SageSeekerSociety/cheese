// 分支保护 (#718) 的数据与动作。拆自 `views/ProjectSettingsView.vue`（#2143）。
//
// 这一块自己取数、自己有四态，理由：GET 要问 GitHub 要一份保护快照，可能比这一页
// 别的请求慢，不能让整页的 Promise.all 陪着等（页面把它和「连接 GitHub 账号」各自
// 占一次 reveal gate）。
//
// 三条规矩跟着这段代码走了一遍：
//
//   1. **每个控件一次 PUT，且是乐观的**。控件绑的是 `bp`；发请求前先把它按这次改动
//      改掉，开关当场就翻过去（省掉一次往返），服务端那份回来覆盖它。失败时还原到发
//      请求前那一份，所有绑 `bp` 的控件跟着弹回原值，再报错。只有一个回滚点（发请求
//      的那一层），不是一处一处各回各的。
//   2. **`approvals_required` 走草稿字符串**。非法输入就地被拒、草稿弹回，坏值
//      永远写不进 `bp`。
//   3. **两个草稿（新检查的名字与路径范围）留在这一层**：添加成功后要清空、失败要
//      留着，而「成功」只有发请求的这一层知道。
import type { BranchProtection, BranchProtectionPatch, ProjectMemberRow } from '@/cx_types'

import { computed, ref } from 'vue'

import { getBranchProtection, listProjectMembers, setBranchProtection } from '@/api'
import { t } from '@/i18n'
import { parseApprovalsInput, parseCheckPaths } from '@/lib/branchProtection'

/** 这一块自己的四态：GET 可能慢到要单独显示加载中/失败。 */
export type BranchProtectionLoadState = 'loading' | 'loaded' | 'error'

export function useBranchProtection(projectId: () => string) {
  const bpLoadState = ref<BranchProtectionLoadState>('loading')
  const bpLoadError = ref<string | null>(null)
  const bp = ref<BranchProtection | null>(null)
  const bpMembers = ref<ProjectMemberRow[]>([])
  const bpError = ref<string | null>(null)
  // Which rule's save is in flight ('' = none): every control disables while any
  // save runs (same as the pool rows), the spinner sits on the one being saved.
  const bpSaving = ref<string | null>(null)
  const newCheckName = ref('')
  const newCheckPaths = ref('')
  // approvals_required is edited through a draft string so an invalid entry can
  // be rejected and snapped back without ever writing a bad value into bp.
  const approvalsDraft = ref('1')

  // GitHub 自己开了保护时，平台的同名规则灰掉（#718 拍板②：两处都能改就是两套
  // 配置）。同名 = 出现在 GitHub 分支保护那一页的规则：必须通过的检查、跟上
  // main、作废已有采纳、批准人数、放行名单。自动合并和任务默认 reviewer 是平台
  // 自己的概念，保持可编。status === 'unknown' 查不到 ≠ 已开启，不灰。
  const ghEnforced = computed(() => bp.value?.github_protection.enforced ?? false)
  const bpBusy = computed(() => bpSaving.value !== null)

  const bpMemberItems = computed(() =>
    bpMembers.value
      .filter((m) => !m.agent)
      .map((m) => ({
        title: m.name
          ? t('work.projectSettings.merge.memberOption', { name: m.name, handle: m.user_handle })
          : m.user_handle,
        value: m.user_handle,
      }))
  )
  const bpReviewerItems = computed(() => [
    { title: t('work.projectSettings.merge.unassigned'), value: '' },
    ...bpMemberItems.value,
  ])

  async function loadBranchProtection() {
    bpLoadState.value = 'loading'
    bpLoadError.value = null
    try {
      const [rules, membersP] = await Promise.all([getBranchProtection(projectId()), listProjectMembers(projectId())])
      bp.value = rules
      bpMembers.value = membersP.data
      approvalsDraft.value = String(rules.approvals_required)
      bpLoadState.value = 'loaded'
    } catch (e) {
      bpLoadError.value = e instanceof Error ? e.message : t('work.projectSettings.merge.loadFailed')
      bpLoadState.value = 'error'
    }
  }

  // One PUT per control change, and optimistic: apply the patch to bp before the
  // request, so the switch flips at once instead of waiting a round-trip. The
  // server's answer replaces it; on failure restore the pre-request bp — every
  // control (all bound to bp, never to local copies) snaps back with it.
  async function saveBranchProtection(patch: BranchProtectionPatch, key: string): Promise<boolean> {
    if (!bp.value) return false
    bpError.value = null
    bpSaving.value = key
    const before = bp.value
    bp.value = { ...before, ...patch }
    try {
      const rules = await setBranchProtection(projectId(), patch)
      bp.value = { ...bp.value, ...rules }
      approvalsDraft.value = String(rules.approvals_required)
      return true
    } catch (e) {
      bp.value = before
      bpError.value = e instanceof Error ? e.message : t('work.projectSettings.merge.saveFailed')
      return false
    } finally {
      bpSaving.value = null
    }
  }

  async function addRequiredCheck() {
    if (!bp.value) return
    const name = newCheckName.value.trim()
    if (!name) return
    const paths = parseCheckPaths(newCheckPaths.value)
    const next = [...bp.value.required_checks, paths.length ? { name, paths } : { name }]
    if (await saveBranchProtection({ required_checks: next }, 'required_checks')) {
      newCheckName.value = ''
      newCheckPaths.value = ''
    }
  }

  async function removeRequiredCheck(index: number) {
    if (!bp.value) return
    const next = bp.value.required_checks.filter((_, i) => i !== index)
    await saveBranchProtection({ required_checks: next }, 'required_checks')
  }

  async function saveApprovals() {
    if (!bp.value) return
    const parsed = parseApprovalsInput(approvalsDraft.value)
    if (parsed === null) {
      bpError.value = t('work.projectSettings.merge.approvalsInvalid')
      approvalsDraft.value = String(bp.value.approvals_required)
      return
    }
    if (parsed === bp.value.approvals_required) return
    if (!(await saveBranchProtection({ approvals_required: parsed }, 'approvals_required'))) {
      approvalsDraft.value = String(bp.value.approvals_required)
    }
  }

  async function saveOverrideHandles(handles: string[]) {
    // 空名单 = 回到缺省（owner + lead），后端用 null 表达。
    await saveBranchProtection({ override_handles: handles.length ? handles : null }, 'override_handles')
  }

  return {
    bpLoadState,
    bpLoadError,
    bp,
    bpError,
    bpSaving,
    newCheckName,
    newCheckPaths,
    approvalsDraft,
    ghEnforced,
    bpBusy,
    bpMemberItems,
    bpReviewerItems,
    loadBranchProtection,
    saveBranchProtection,
    addRequiredCheck,
    removeRequiredCheck,
    saveApprovals,
    saveOverrideHandles,
  }
}
