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
// 出题人下拉只是一支框：选项和「正在读」都由页面递进来（场景规则见
// docs/manual/dev/scenes.md）。原来它自己按 spaceId + filters 去拉，取数挪到页面后
// 它才和别的下拉一样，给 props 就能单独画。
import { useI18n } from 'vue-i18n'

defineProps<{
  items: Array<{ title: string; value: number | null }>
  loading: boolean
}>()

const model = defineModel<number | null>({ required: true })

const { t } = useI18n()
</script>
