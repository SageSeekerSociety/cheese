<template>
  <!-- 六格共用的一行筛选：时间段、日期、分类、审核状态。改了要点「应用」才生效 ——
       每一格都要重新拉数，边改边拉会把半截条件发出去。 -->
  <div class="afb">
    <div class="afb__presets" role="group" :aria-label="t('spaces.analytics.filter.presetLabel')">
      <button
        v-for="preset in presets"
        :key="preset.key"
        type="button"
        class="afb__preset"
        @click="$emit('apply-preset', preset.key)"
      >
        {{ t(`spaces.analytics.filter.preset.${preset.key}`) }}
      </button>
    </div>

    <v-text-field
      v-model="model.from"
      class="afb__date"
      type="date"
      :aria-label="t('spaces.analytics.filter.from')"
      :prefix="t('spaces.analytics.filter.from')"
      density="compact"
      hide-details
      variant="outlined"
    />
    <v-text-field
      v-model="model.to"
      class="afb__date"
      type="date"
      :aria-label="t('spaces.analytics.filter.to')"
      :prefix="t('spaces.analytics.filter.to')"
      density="compact"
      hide-details
      variant="outlined"
    />
    <v-select
      v-model="model.categoryId"
      class="afb__select"
      autocomplete="off"
      :items="categoryItems"
      :aria-label="t('spaces.analytics.filter.category')"
      :prefix="t('spaces.analytics.filter.category')"
      density="compact"
      hide-details
      variant="outlined"
    />
    <v-select
      v-model="model.taskApproved"
      class="afb__select"
      autocomplete="off"
      :items="approvalItems"
      :aria-label="t('spaces.analytics.filter.approval')"
      :prefix="t('spaces.analytics.filter.approval')"
      density="compact"
      hide-details
      variant="outlined"
    />

    <div class="afb__actions">
      <BaseButton kind="ghost" @click="$emit('reset')">{{ t('spaces.analytics.filter.reset') }}</BaseButton>
      <BaseButton kind="primary" @click="$emit('apply')">
        {{ t('spaces.analytics.filter.apply') }}
      </BaseButton>
    </div>
  </div>
</template>

<script setup lang="ts">
import type { SpaceAnalyticsQueryState } from '../utils'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import BaseButton from '@/components/base/BaseButton.vue'

const model = defineModel<SpaceAnalyticsQueryState>({ required: true })

defineProps<{
  categoryItems: Array<{ title: string; value: number | null }>
}>()

defineEmits<{
  (e: 'apply'): void
  (e: 'reset'): void
  (e: 'apply-preset', preset: '30d' | '180d' | 'all'): void
}>()

const { t } = useI18n()

const approvalItems = computed(() =>
  (['ALL', 'NONE', 'APPROVED', 'DISAPPROVED'] as const).map((value) => ({
    title: t(`spaces.analytics.filter.approvalOption.${value}`),
    value,
  }))
)

const presets = [{ key: '30d' as const }, { key: '180d' as const }, { key: 'all' as const }]
</script>

<style scoped>
.afb {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}

.afb__presets {
  display: inline-flex;
  gap: 2px;
  padding: 2px;
  border-radius: var(--radius-md);
  background: var(--fill-2);
}

.afb__preset {
  height: 28px;
  padding: 0 10px;
  border-radius: var(--radius-sm);
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
  white-space: nowrap;
  transition:
    background-color var(--dur-quick) var(--ease-standard),
    color var(--dur-quick) var(--ease-standard);
}

.afb__preset:hover {
  background: var(--surface);
  color: var(--ink);
}

.afb__preset:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: 1px;
}

.afb__date {
  flex: 0 1 168px;
  min-width: 150px;
}

.afb__select {
  flex: 0 1 172px;
  min-width: 140px;
}

.afb__actions {
  display: flex;
  gap: 4px;
  margin-left: auto;
}

.afb :deep(.v-field) {
  font-size: 13px;
}

.afb :deep(.v-text-field__prefix) {
  color: var(--muted);
}
</style>
