// 「转让项目」那颗按钮背后的一次交手：`POST /projects/{id}/transfer`，把项目交给项目里
// 的一个人（#3830）。对方接受之前什么都不变，所以成了之后只刷这个项目（它带着那份
// 「等待接受」的转让），用 allSettled：转让已经发出去了，刷不成功不该把它变成失败。
//
// 拆自 `components/TransferProjectDialog.vue`：动作在这一层，组件不直接碰 API 层
// （`.claude/rules/architecture.md`）。
import type { MaybeRefOrGetter } from 'vue'

import { ref, toValue } from 'vue'

import { offerTransfer } from '@/api'
import { t } from '@/i18n'
import { queryClient } from '@/query/client'
import { projectQuery } from '@/query/project'

export function useProjectTransfer(projectId: MaybeRefOrGetter<string>) {
  /** 正在交出去：按钮上的 loading。 */
  const transferring = ref(false)
  const error = ref<string | null>(null)

  /**
   * 交给 `handle` 那一个人。成功返回 true —— 调用方这时候就可以把弹窗收掉，项目在
   * 背后刷。失败返回 false 并把理由留在 `error` 里，弹窗不关：那句理由正是用户
   * 要的下一步。
   */
  async function transfer(handle: string): Promise<boolean> {
    transferring.value = true
    error.value = null
    try {
      await offerTransfer(toValue(projectId), handle)
    } catch (e) {
      error.value = e instanceof Error ? e.message : t('work.projectTransfer.failed')
      transferring.value = false
      return false
    }
    void Promise.allSettled([
      queryClient.invalidateQueries({ queryKey: projectQuery(toValue(projectId)).queryKey }),
    ]).finally(() => {
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
