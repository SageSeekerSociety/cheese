// 活动图上点一格，下面的话题列表就只看那一周；再点一次同一周，或者点「回到最近」，
// 回到最近参与的那几个。换了一个人，上一个人选中的那一周不跟过来。
import type { ProfileTopic } from '@/cx_types'
import type { ActivityWeek } from '@/lib/activityYear'

import { computed, ref, watch } from 'vue'

import { getUserTopics } from '@/api'
import { t } from '@/i18n'
import { formatUtcDay } from '@/lib/activityYear'

/** 选中一周时列那一周的全部（接口上限 50）。 */
export const WEEK_TOPICS = 50

/**
 * @param handle 这一页是谁。
 * @param recent 最近参与的那几个；没读到（还在读、或者这一段单独失败）时给 null。
 * @param locale 写那一周的日期用。
 */
export function useProfileTopics(
  handle: () => string,
  recent: () => ProfileTopic[] | null | undefined,
  locale: () => string
) {
  const selectedWeek = ref<ActivityWeek | null>(null)
  const weekTopics = ref<ProfileTopic[] | null>(null)
  const weekPending = ref(false)
  const weekFailed = ref(false)
  let weekRequest = 0

  function clearWeek() {
    weekRequest += 1
    selectedWeek.value = null
    weekTopics.value = null
    weekPending.value = false
    weekFailed.value = false
  }

  async function pickWeek(week: ActivityWeek) {
    if (selectedWeek.value?.from === week.from) {
      clearWeek()
      return
    }
    const seq = ++weekRequest
    selectedWeek.value = week
    weekPending.value = true
    weekFailed.value = false
    try {
      const { topics } = await getUserTopics(handle(), { from: week.from, to: week.to, limit: WEEK_TOPICS })
      if (seq !== weekRequest) return
      weekTopics.value = topics
    } catch {
      if (seq !== weekRequest) return
      weekTopics.value = null
      weekFailed.value = true
    } finally {
      if (seq === weekRequest) weekPending.value = false
    }
  }

  watch(handle, clearWeek)

  const topicsTitle = computed(() => {
    const week = selectedWeek.value
    if (!week) return t('users.profile.topics.recent')
    const day = (d: string) => formatUtcDay(d, locale(), { month: 'long', day: 'numeric' })
    return t('users.profile.topics.range', { from: day(week.from), to: day(week.to) })
  })

  // 选中一周、新的那一份还没回来时，列表先留着原来的几行（淡一点），不先清空再填满。
  const topics = computed<ProfileTopic[]>(() => {
    if (selectedWeek.value && weekTopics.value) return weekTopics.value
    return recent() ?? []
  })
  const topicsFailed = computed(() => (selectedWeek.value ? weekFailed.value : recent() === null))
  const topicsEmpty = computed(() => !weekPending.value && !topicsFailed.value && topics.value.length === 0)

  return { selectedWeek, weekPending, pickWeek, clearWeek, topicsTitle, topics, topicsFailed, topicsEmpty }
}
