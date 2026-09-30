<template>
  <v-select
    v-model="model"
    autocomplete="off"
    :items="items"
    :loading="loading"
    :prefix="t('spaces.analytics.publisher.label')"
    :aria-label="t('spaces.analytics.publisher.label')"
    density="compact"
    hide-details
    variant="outlined"
  />
</template>

<script setup lang="ts">
import type { SpaceAnalyticsQueryState } from '../utils'

import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { buildAnalyticsApiParams } from '../utils'

import { SpacesApi } from '@/network/api/spaces'

const props = defineProps<{
  spaceId: number
  filters: SpaceAnalyticsQueryState
}>()

const model = defineModel<number | null>({ required: true })

const loading = ref(false)
const { t } = useI18n()

const publishers = ref<Array<{ title: string; value: number | null }>>([])

const params = computed(() => buildAnalyticsApiParams('publishers', props.filters))

const load = async () => {
  loading.value = true
  try {
    const { data } = await SpacesApi.getAnalyticsPublishers(props.spaceId, params.value)
    publishers.value = [
      ...data.publishers.map((publisher) => ({
        title: publisher.publisherName,
        value: publisher.publisherId,
      })),
    ]
  } catch (error) {
    console.error('load analytics publishers failed', error)
  } finally {
    loading.value = false
  }
}

watch(params, () => {
  load().catch(() => undefined)
})

onMounted(() => {
  load().catch(() => undefined)
})

const items = computed(() => [{ title: t('spaces.analytics.publisher.all'), value: null }, ...publishers.value])
</script>
