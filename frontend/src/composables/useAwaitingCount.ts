import { readonly, type Ref, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useEventListener } from '@vueuse/core'

import { listAwaitingMe } from '@/api'
import { firstPaintDone } from '@/lib/afterFirstPaint'

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
 * 窗口重新拿到焦点。离开待办页那一下不受节流——人刚在那儿处理完事情，数字应该
 * 立刻跟上。读失败就保持原来的数，不把一次网络抖动画成「没有待办」。
 */
export function useAwaitingCount(enabled: Ref<boolean>): Ref<number> {
  const route = useRoute()
  let lastAt = 0

  async function refresh(force = false) {
    if (!enabled.value) return
    const now = Date.now()
    if (!force && now - lastAt < MIN_INTERVAL_MS) return
    lastAt = now
    try {
      const items = (await listAwaitingMe()).data
      count.value = items.length
      const perProject: Record<string, number> = {}
      for (const item of items) perProject[item.projectId] = (perProject[item.projectId] ?? 0) + 1
      byProject.value = perProject
    } catch {
      // 保持原数。
    }
  }

  // 登录之后的第一遍排到首帧之后（`lib/afterFirstPaint.ts`）：这条角标几秒后才有人看，
  // 别在开页那一瞬间跟页面自己要的数据抢。之后的刷新照旧——换页面、窗口重新拿到焦点，
  // 都还是当场问（`asked` 挡住的是首帧那个门开了之后重复排的那几遍）。
  //
  // 首帧之前一次都不问，包括「换页面」这条：冷开落在深链上（刷新、从别处点进来）时，
  // 这一次跳转是 `main.ts` 挂载之后才落地的，`from` 是起始的 `/`，看着像人换了页，其实
  // 就是这一次打开本身——放它过去就会在首帧之前读一遍，门开了再读一遍。门开那一次读的
  // 已经是落地后的状态，不会漏。
  let asked = false
  watch(
    [enabled, firstPaintDone],
    ([on, painted]) => {
      if (!on) {
        count.value = 0
        byProject.value = {}
        asked = false
        return
      }
      if (!painted || asked) return
      asked = true
      void refresh(true)
    },
    { immediate: true }
  )
  watch(
    () => route.path,
    (_path, from) => {
      if (!firstPaintDone.value) return
      void refresh(from === '/inbox')
    }
  )
  useEventListener(window, 'focus', () => void refresh())

  return count
}
