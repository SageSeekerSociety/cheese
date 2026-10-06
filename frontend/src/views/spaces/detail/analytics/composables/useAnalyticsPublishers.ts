import type { Ref } from 'vue'
import type { SpaceAnalyticsQueryState } from '../utils'

import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { buildAnalyticsApiParams } from '../utils'

import { SpacesApi } from '@/network/api/spaces'

/**
 * 出题人下拉的选项：从前 `AnalyticsPublisherSelect` 自己在组件里拉，取数把一颗纯
 * 展示的下拉拖成了 C 级。现在盒子只收 `items` 和 `loading`，这段按筛选拉选项的活
 * 归页面（容器）——和数据几格别的取数在同一层。
 */
export function useAnalyticsPublishers(spaceId: Ref<number>, filters: Ref<SpaceAnalyticsQueryState>) {
  const { t } = useI18n()
  const loading = ref(false)
  const publishers = ref<Array<{ title: string; value: number | null }>>([])

  const params = computed(() => buildAnalyticsApiParams('publishers', filters.value))

  const load = async () => {
    loading.value = true
    try {
      const { data } = await SpacesApi.getAnalyticsPublishers(spaceId.value, params.value)
      publishers.value = data.publishers.map((publisher) => ({
        title: publisher.publisherName,
        value: publisher.publisherId,
      }))
    } catch (error) {
      console.error('load analytics publishers failed', error)
    } finally {
      loading.value = false
    }
  }

  watch(
    params,
    () => {
      load().catch(() => undefined)
    },
    { immediate: true }
  )

  const items = computed(() => [{ title: t('spaces.analytics.publisher.all'), value: null }, ...publishers.value])

  return { items, loading, reload: load }
}
