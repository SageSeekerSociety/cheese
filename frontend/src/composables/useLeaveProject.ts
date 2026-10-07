// 「退出项目」那颗按钮背后的一整件事：`DELETE /projects/{id}/membership`、刷新名册和
// 项目列表、离开这个项目。
//
// 拆自 `components/LeaveProjectDialog.vue`：动作在这一层，组件只管画和接线 —— 组件不
// 直接碰 API 层（`.claude/rules/architecture.md`）。
import type { MaybeRefOrGetter } from 'vue'

import { ref, toValue } from 'vue'

import { useNavigation } from '@/composables/useNavigation'

import { leaveProject } from '@/api'
import { t } from '@/i18n'
import { useWorkspaceStore } from '@/stores/workspace'

export function useLeaveProject(projectId: MaybeRefOrGetter<string>) {
  const navigation = useNavigation()
  const store = useWorkspaceStore()

  /** 正在退：确认框上那颗按钮的 loading。 */
  const leaving = ref(false)
  const error = ref<string | null>(null)

  /**
   * 退出。成功返回 true —— 调用方这时候就可以把确认框收掉，刷新和跳转在背后做；失败
   * 返回 false 并把理由留在 `error` 里，框不关：人还没退成，「取消」仍然有意义，而那句
   * 话正是他要的下一步。
   */
  async function leave(): Promise<boolean> {
    leaving.value = true
    error.value = null
    try {
      await leaveProject(toValue(projectId))
    } catch (e) {
      // 只有退出本身失败才算是失败。拒绝的理由（需要先转让、还是某个话题唯一的 owner）
      // 原样留给用户。
      error.value = e instanceof Error ? e.message : t('project.leave.failed')
      leaving.value = false
      return false
    }
    // 退出的那一刻，这条请求已经成功了：**接下来做什么都不能再把它变成失败**。
    // 两份刷新是为了让别的页面不拿着旧数据把我送回这个项目（名册里没有我了，项目
    // 列表里也没有这个项目了），但它们是锦上添花 —— 刷新接口抖一下，用 allSettled
    // 让失败就地咽掉，人照样是退出成功的，照样该离开。用 Promise.all 的话一次刷新
    // 失败会挂出「退出失败」，而人其实已经退掉了 —— 他再点一次只会拿到 409。
    void Promise.allSettled([store.refreshMembers(), store.refreshProjects()]).then(() => {
      // replace：退出成功后再按回退键，人不该又落回这个项目 —— 名册里已经没有他了。
      navigation?.navigate({ name: 'HomeSpaces' }, { replace: true })
      leaving.value = false
    })
    return true
  }

  /** 重新打开时清掉上一次留下的理由。 */
  function clearError() {
    error.value = null
  }

  return { leaving, error, leave, clearError }
}
