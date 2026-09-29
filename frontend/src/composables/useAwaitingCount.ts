import { type Ref, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useEventListener } from '@vueuse/core'

import { listAwaitingMe } from '@/api'

/** 两次读之间至少隔这么久：换路由很频繁，而这个数字晚几十秒变不要紧。 */
const MIN_INTERVAL_MS = 30_000

/**
 * 「待办」那一格的件数：桌面 rail 与手机底栏各画一颗角标。
 *
 * 和待办页读同一个接口（`/awaiting-me`），所以角标上的数就是点进去看到的行数。
 * 没有推送通道告诉它「有新的一件」，于是在人**做了点什么**的时候再读：换页面、
 * 窗口重新拿到焦点。离开待办页那一下不受节流——人刚在那儿处理完事情，数字应该
 * 立刻跟上。读失败就保持原来的数，不把一次网络抖动画成「没有待办」。
 */
export function useAwaitingCount(enabled: Ref<boolean>): Ref<number> {
  const count = ref(0)
  const route = useRoute()
  let lastAt = 0

  async function refresh(force = false) {
    if (!enabled.value) return
    const now = Date.now()
    if (!force && now - lastAt < MIN_INTERVAL_MS) return
    lastAt = now
    try {
      count.value = (await listAwaitingMe()).data.length
    } catch {
      // 保持原数。
    }
  }

  watch(
    enabled,
    (on) => {
      if (on) void refresh(true)
      else count.value = 0
    },
    { immediate: true }
  )
  watch(
    () => route.path,
    (_path, from) => void refresh(from === '/inbox')
  )
  useEventListener(window, 'focus', () => void refresh())

  return count
}
