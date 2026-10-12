import { computed, type MaybeRefOrGetter, ref, toValue, watch } from 'vue'

import { SpacesApi } from '@/api/spaces'
import { buildAnalyticsApiParams, type SpaceAnalyticsQueryState } from '@/views/spaces/detail/analytics/utils'

/** The analytics page fetches options; the select itself only renders props. */
export function useAnalyticsPublishers(
  spaceId: MaybeRefOrGetter<number>,
  filters: MaybeRefOrGetter<SpaceAnalyticsQueryState>
) {
  const publishers = ref<Array<{ title: string; value: number | null }>>([])
  const loading = ref(false)
  const params = computed(() => buildAnalyticsApiParams('publishers', toValue(filters)))
  const load = async () => {
    loading.value = true
    try {
      const data = await SpacesApi.getAnalyticsPublishers(toValue(spaceId), params.value)
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
  watch(params, () => void load(), { immediate: true })
  return { publishers, loading }
}
