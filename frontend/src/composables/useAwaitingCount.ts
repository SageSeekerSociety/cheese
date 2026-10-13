import { onScopeDispose, readonly, type Ref, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useEventListener } from '@vueuse/core'

import { listAwaitingMe } from '@/api'
import { onAwaitingChanged } from '@/api/awaitingChanged'

/** 两次读之间至少隔这么久：换路由很频繁，而这个数字晚几十秒变不要紧。 */
const MIN_INTERVAL_MS = 30_000

// 全站一份：首页那一格和首页侧栏的「待办」那一行读的是同一个数。
const count = ref(0)

// 同一个数按项目拆开：桌面 rail 上每个项目格子画自己那几件。和总数同一次读算出来，
// 不再多打一个请求——待我处理的每一件都带着 projectId。
const byProject = ref<Record<string, number>>({})

/** 只读这个数，不负责去取——取数由 App 里那一处 `useAwaitingCount` 管。 */
export function awaitingCount(): Readonly<Ref<number>> {
  return readonly(count)
}

/** 每个项目待我处理的件数；没有的项目不在表里。rail 上项目格子的角标读它。 */
export function awaitingCountByProject(): Readonly<Ref<Record<string, number>>> {
  return readonly(byProject)
}

/**
 * 待我处理的件数：桌面首页那一格与手机底栏「待办」各画一颗角标。
 *
 * 和待办页读同一个接口（`/awaiting-me`），所以角标上的数就是点进去看到的行数。
 * 没有推送通道告诉它「有新的一件」，于是在人**做了点什么**的时候再读：换页面、
 * 窗口重新拿到焦点。离开待办页那一下、以及人在哪儿做成了一件会了结待办的事（拍板、
 * 回答提问、采纳或退回……那些端点自己会说，见 `api/awaitingChanged.ts`）不受节流——
 * 人刚处理完事情，数字应该立刻跟上。读失败就保持原来的数，不把一次网络抖动画成
 * 「没有待办」。
 *
 * 几次读可能同时在路上（连着两下操作），回来的先后不一定是发出去的先后：只认最后
 * 发出去的那一次，早发的晚到就丢掉，免得旧数盖掉新数。
 */
export function useAwaitingCount(enabled: Ref<boolean>): Ref<number> {
  const route = useRoute()
  let lastAt = 0
  // 每发一次读加一；回来时不等于它，说明后面又发过一次，这一份已经过时。
  let latest = 0

  async function refresh(force = false) {
    if (!enabled.value) return
    const now = Date.now()
    if (!force && now - lastAt < MIN_INTERVAL_MS) return
    lastAt = now
    const mine = ++latest
    try {
      const items = (await listAwaitingMe()).data
      if (mine !== latest) return
      count.value = items.length
      const perProject: Record<string, number> = {}
      for (const item of items) perProject[item.projectId] = (perProject[item.projectId] ?? 0) + 1
      byProject.value = perProject
    } catch {
      // 保持原数。
    }
  }

  watch(
    enabled,
    (on) => {
      if (on) void refresh(true)
      else {
        latest += 1 // 退出登录前发出去的读，回来也不再写。
        count.value = 0
        byProject.value = {}
      }
    },
    { immediate: true }
  )
  watch(
    () => route.path,
    (_path, from) => void refresh(from === '/inbox')
  )
  useEventListener(window, 'focus', () => void refresh())
  onScopeDispose(onAwaitingChanged(() => void refresh(true)))

  return count
}
