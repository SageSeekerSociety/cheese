<script setup lang="ts">
/**
 * 表格底下那一条分页：「1–10 / 37」加上一页、下一页。放进 BaseTable 的 `#foot` 槽。
 * 只管页码，切片由页面做（`rows.slice((page - 1) * perPage, page * perPage)`）。
 * 只有一页时整条不画。
 */
import { computed } from 'vue'

import BaseButton from './BaseButton.vue'

import { t } from '@/i18n'

const props = withDefaults(defineProps<{ total: number; perPage?: number }>(), { perPage: 10 })
const page = defineModel<number>('page', { default: 1 })

const pageCount = computed(() => Math.max(1, Math.ceil(props.total / props.perPage)))
const from = computed(() => (props.total ? (page.value - 1) * props.perPage + 1 : 0))
const to = computed(() => Math.min(props.total, page.value * props.perPage))
</script>

<template>
  <nav v-if="pageCount > 1" class="tpager" :aria-label="t('global.table.pager')">
    <span class="tpager__range t-num">{{ t('global.table.range', { from, to, total }) }}</span>
    <BaseButton
      size="sm"
      icon="mdi-chevron-left"
      :aria-label="t('global.table.prev')"
      :disabled="page <= 1"
      @click="page = page - 1"
    />
    <BaseButton
      size="sm"
      icon="mdi-chevron-right"
      :aria-label="t('global.table.next')"
      :disabled="page >= pageCount"
      @click="page = page + 1"
    />
  </nav>
</template>

<style scoped>
.tpager {
  display: flex;
  gap: 4px;
  align-items: center;
  justify-content: flex-end;
  padding: 6px 8px;
}

.tpager__range {
  margin-inline-end: 8px;
  color: var(--muted);
  font-size: 12.5px;
}
</style>
