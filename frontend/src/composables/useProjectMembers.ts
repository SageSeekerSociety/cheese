// 读某个项目的名册。「退出项目」的确认框和「转让项目」的对话框都要在打开时读一次
// **要操作的那个**项目的名册 —— 从项目列表右键打开时它可能不是正开着的那个，store
// 里那份名册是正开着那个的。
//
// 拆自 `components/TransferProjectDialog.vue` 和 `components/LeaveProjectDialog.vue`：
// 取数在这一层，组件不直接碰 API 层（`.claude/rules/architecture.md`）。
import type { MaybeRefOrGetter } from 'vue'
import type { ProjectMemberRow } from '@/cx_types'

import { ref, toValue, watch } from 'vue'

import { listProjectMembers } from '@/api'

export function useProjectMembers(projectId: MaybeRefOrGetter<string>) {
  const rows = ref<ProjectMemberRow[]>([])

  // 换了项目就把上一份清掉：留着的是另一个项目的名册，会让人以为读到的就是眼前这个。
  watch(
    () => toValue(projectId),
    () => {
      rows.value = []
    }
  )

  /**
   * 退回「还不知道」。打开对话框时先叫一次：上一次那一份是同项目的旧数据，措辞不该
   * 拿它先顶上，等这一次读回来再说。
   */
  function reset() {
    rows.value = []
  }

  /**
   * 读一次名册。`wanted` 在结果回来那一刻判：项目在途中换了、对话框已经关了，这次
   * 结果就不写进去（读还是读了）。读不到时把错误扔出去 —— 两处要的措辞不一样：一处
   * 报在弹窗里，一处只是少说一句话。
   */
  async function load(wanted: () => boolean = () => true): Promise<void> {
    const pid = toValue(projectId)
    const payload = await listProjectMembers(pid)
    if (toValue(projectId) === pid && wanted()) rows.value = payload.data
  }

  return { rows, load, reset }
}
