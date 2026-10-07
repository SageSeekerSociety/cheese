// 「转让项目」那颗按钮背后的一次交手：`PUT /projects/{id}/owner`，成了之后刷新项目行
// 和名册。
//
// 项目行必须重新拉一遍 —— `owner_handle` 换了人，团队也可能跟着换；名册也跟着刷，界
// 面上的「退出 / 转让」两颗按钮才是按新身份长的。两个都用 allSettled：转让已经做成
// 了，刷不成功不该把它变成失败。
//
// 拆自 `components/TransferProjectDialog.vue`：动作在这一层，组件不直接碰 API 层
// （`.claude/rules/architecture.md`）。
import type { MaybeRefOrGetter } from 'vue'

import { ref, toValue } from 'vue'

import { setProjectOwner } from '@/api'
import { t } from '@/i18n'
import { useWorkspaceStore } from '@/stores/workspace'

export function useProjectTransfer(projectId: MaybeRefOrGetter<string>) {
  const store = useWorkspaceStore()

  /** 正在交出去：按钮上的 loading。 */
  const transferring = ref(false)
  const error = ref<string | null>(null)

  /**
   * 交给 `handle` 那一个人。成功返回 true —— 调用方这时候就可以把弹窗收掉，项目行和
   * 名册在背后刷。失败返回 false 并把理由留在 `error` 里，弹窗不关：那句理由正是用户
   * 要的下一步。
   */
  async function transfer(handle: string): Promise<boolean> {
    transferring.value = true
    error.value = null
    try {
      await setProjectOwner(toValue(projectId), handle)
    } catch (e) {
      error.value = e instanceof Error ? e.message : t('work.projectTransfer.failed')
      transferring.value = false
      return false
    }
    void Promise.allSettled([store.refreshProjects(), store.refreshMembers()]).finally(() => {
      transferring.value = false
    })
    return true
  }

  /** 重新打开时清掉上一次留下的理由。 */
  function clearError() {
    error.value = null
  }

  return { transferring, error, transfer, clearError }
}
