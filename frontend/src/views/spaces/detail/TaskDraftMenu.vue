<template>
  <v-menu location="bottom start">
    <template #activator="{ props: menu }">
      <BaseButton v-bind="menu" kind="ghost" size="sm" append-icon="mdi-chevron-down" data-testid="draft-menu">
        {{ t('spaces.detail.publishTask.draftsMenu.label') }}
        <span class="tdm__n t-num">{{ drafts.length }}</span>
      </BaseButton>
    </template>
    <v-list
      density="compact"
      min-width="280"
      max-width="360"
      :aria-label="t('spaces.detail.publishTask.draftsMenu.heading')"
    >
      <v-list-subheader>{{ t('spaces.detail.publishTask.draftsMenu.heading') }}</v-list-subheader>
      <v-list-item
        v-for="draft in drafts"
        :key="draft.id"
        :active="draft.id === currentId"
        @click="emit('open', draft.id)"
      >
        <v-list-item-title class="tdm__row">
          <span v-if="draft.taskId" class="tdm__kind">{{ t('spaces.detail.publishTask.draftsMenu.edit') }}</span>
          <span class="tdm__name" data-user-content>{{
            draft.name || draft.taskName || t('spaces.detail.publishTask.drafts.untitled')
          }}</span>
        </v-list-item-title>
        <template #append>
          <span class="tdm__when t-meta-read">{{ when(draft.updatedAt) }}</span>
        </template>
      </v-list-item>
      <v-divider v-if="drafts.length" class="my-1" />
      <v-list-item
        prepend-icon="mdi-plus"
        :title="t('spaces.detail.publishTask.draftsMenu.new')"
        @click="emit('new')"
      />
    </v-list>
  </v-menu>
</template>

<script setup lang="ts">
// 发题页顶栏的「草稿」：我在这个空间的草稿，点哪份打开哪份；最下面「新建草稿」。对已发布
// 题目的修改也在里面，标着「修改」。只拿 props、只发事件。
import type { TaskDraftSummary } from '@/types'

import { useI18n } from 'vue-i18n'

import BaseButton from '@/components/base/BaseButton.vue'
import { relTime } from '@/lib/relTime'

defineProps<{
  drafts: TaskDraftSummary[]
  currentId: number | null
}>()

const emit = defineEmits<{
  open: [id: number]
  new: []
}>()

const { t } = useI18n()

function when(at: string): string {
  return relTime(at)
}
</script>

<style scoped>
.tdm__n {
  margin-left: 4px;
  color: var(--muted);
}

.tdm__row {
  display: flex;
  gap: 6px;
  align-items: center;
  min-width: 0;
}

.tdm__kind {
  flex: none;
  padding: 0 6px;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
  background: var(--fill);
  border-radius: var(--radius-sm);
}

.tdm__name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tdm__when {
  margin-left: 12px;
  white-space: nowrap;
}
</style>
