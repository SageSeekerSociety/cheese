// 一个空间的公告，公告页和题目列表顶上那一栏共用。
//
// 哪些算「当前」、哪些已到期由服务端按它的时钟分好（`current` / `expired`），这里
// 不自己比时间。
import type { SpaceAnnouncement } from '@/types'

import { computed, ref, watch } from 'vue'

import { SpacesApi } from '@/network/api/spaces'

export function useSpaceAnnouncements(spaceId: () => number) {
  const current = ref<SpaceAnnouncement[]>([])
  const expired = ref<SpaceAnnouncement[]>([])
  /** 发一条会通知到几个人；不是管理员时为 null。 */
  const notifyCount = ref<number | null>(null)
  const loaded = ref(false)
  /** 上一次没读到的那个错；读成功就清掉。留着错本身，页面才分得出「没权限」和「出错了」。 */
  const error = ref<unknown>(null)
  const failed = computed(() => error.value !== null)

  async function reload() {
    const id = spaceId()
    if (!id) return
    try {
      const { data } = await SpacesApi.listAnnouncements(id)
      if (id !== spaceId()) return
      current.value = data.current
      expired.value = data.expired
      notifyCount.value = data.notifyCount
      error.value = null
    } catch (e) {
      if (id !== spaceId()) return
      console.error('获取公告失败:', e)
      error.value = e
    } finally {
      loaded.value = true
    }
  }

  watch(
    spaceId,
    () => {
      current.value = []
      expired.value = []
      notifyCount.value = null
      loaded.value = false
      error.value = null
      void reload()
    },
    { immediate: true }
  )

  return { current, expired, notifyCount, loaded, error, failed, reload }
}
