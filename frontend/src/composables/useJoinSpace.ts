// 「用邀请码加入一个空间」这一件事本身：问后端、成了就进那个空间。
//
// 加入之后直接进那个空间：刚加入的空间里通常还没有他的东西，留在原地等于什么都没发生。
// 两个入口共用这一份 —— 首页那份目录里的「用邀请码加入空间」那一行，和待办页上「一个
// 项目都没有」那一屏。放进来的理由和别处一样：弹窗只吃 props 出事件（A 档），谁去问、
// 成了往哪跳，归这一层。
import { ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { SpacesApi } from '@/api/spaces'
import { t } from '@/i18n'
import { spaceEntryRoute } from '@/lib/spaceEntry'

export function useJoinSpace(onJoined?: () => void) {
  const router = useRouter()
  const open = ref(false)
  const busy = ref(false)
  const error = ref('')

  // 上一次失败留下的那句理由不该跟到下一次：重开就是重来一遍。
  watch(open, (now) => {
    if (now) error.value = ''
  })

  async function join(code: string) {
    if (busy.value) return
    busy.value = true
    error.value = ''
    try {
      const data = await SpacesApi.join({ code })
      open.value = false
      onJoined?.()
      await router.push(spaceEntryRoute(data.space))
    } catch {
      error.value = t('work.joinFailed')
    } finally {
      busy.value = false
    }
  }

  return { open, busy, error, join }
}
