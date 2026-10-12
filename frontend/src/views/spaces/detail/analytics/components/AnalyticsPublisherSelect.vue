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
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

const props = defineProps<{
  publishers: Array<{ title: string; value: number | null }>
  loading: boolean
}>()
const model = defineModel<number | null>({ required: true })
const { t } = useI18n()
const items = computed(() => [{ title: t('spaces.analytics.publisher.all'), value: null }, ...props.publishers])
</script>
