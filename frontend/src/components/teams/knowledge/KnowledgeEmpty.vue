<script setup lang="ts">
// 一条资料都没有时那一块。
//
// 两句话分岔：筛过（搜索框 / 类型 / 标签里有东西）说的是「换个条件试试」，
// 没筛过说的是「先去聊天里放点东西进来」—— 这两种「空」不是同一件事，
// 说错哪一种都会让人以为是坏了。判据由页递进来（`hasFilters`）。
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import { t } from '@/i18n'

defineOptions({ name: 'KnowledgeEmpty' })

defineProps<{ hasFilters: boolean }>()
</script>

<template>
  <div class="empty-knowledge-state">
    <BaseEmptyState
      icon="mdi-book-open-page-variant"
      :title="t('teams.knowledge.emptyTitle')"
      :desc="hasFilters ? t('teams.knowledge.emptyFiltered') : t('teams.knowledge.emptyHint')"
    />
  </div>
</template>

<style scoped lang="scss">
.empty-knowledge-state {
  border: 1px dashed var(--line-2);
  border-radius: var(--radius-lg);
  background-color: var(--fill);
}
</style>
