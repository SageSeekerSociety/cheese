<template>
  <PageHeader show-on-mobile :title="t('spaces.detail.myDrafts.title')">
    <template #actions>
      <BaseButton kind="primary" prepend-icon="mdi-plus" :to="publishTo">{{
        t('spaces.detail.tasks.publishTask')
      }}</BaseButton>
    </template>
  </PageHeader>

  <div class="mdr">
    <BaseTable
      :cols="[null, '28%', '128px', '96px']"
      :label="t('spaces.detail.myDrafts.title')"
      :loading="loading && !drafts.length"
      :state="failed ? 'error' : 'rows'"
      :empty="!loading && !failed && !drafts.length ? t('spaces.detail.myDrafts.empty') : null"
      cards
      min-width="560px"
    >
      <template #head>
        <tr>
          <th scope="col">{{ t('spaces.detail.myDrafts.name') }}</th>
          <th scope="col">{{ t('spaces.detail.myDrafts.source') }}</th>
          <th scope="col">{{ t('spaces.detail.myDrafts.updated') }}</th>
          <th scope="col">
            <span class="mdr__sr">{{ t('spaces.detail.myDrafts.actions') }}</span>
          </th>
        </tr>
      </template>
      <template #error>
        <BaseLoadError :title="t('spaces.detail.myDrafts.loadFailed')" @retry="emit('retry')" />
      </template>
      <tr v-for="draft in drafts" :key="draft.id" :data-testid="`my-draft-${draft.id}`">
        <td data-card="primary">
          <button type="button" class="mdr__open" @click="emit('open', draft)">
            <span v-if="draft.taskId" class="mdr__kind">{{ t('spaces.detail.publishTask.draftsMenu.edit') }}</span>
            <span class="mdr__name" data-user-content>{{ nameOf(draft) }}</span>
          </button>
        </td>
        <td :data-label="t('spaces.detail.myDrafts.source')" class="mdr__muted" data-user-content>
          {{ draft.source ? t('spaces.detail.myDrafts.fromFile', { file: draft.source }) : '—' }}
        </td>
        <td :data-label="t('spaces.detail.myDrafts.updated')" class="mdr__muted">{{ relTime(draft.updatedAt) }}</td>
        <td :data-label="t('spaces.detail.myDrafts.actions')">
          <BaseButton
            size="sm"
            kind="ghost"
            :aria-label="t('spaces.detail.publishTask.strip.removeOne', { name: nameOf(draft) })"
            :data-testid="`my-draft-remove-${draft.id}`"
            @click="asking = draft"
            >{{ t('spaces.detail.publishTask.strip.remove') }}</BaseButton
          >
        </td>
      </tr>
    </BaseTable>
  </div>

  <ConfirmDialog
    :model-value="asking !== null"
    :title="t('spaces.detail.myDrafts.removeAsk', { name: asking ? nameOf(asking) : '' })"
    :confirm-label="t('spaces.detail.publishTask.strip.remove')"
    danger
    :loading="removing"
    @update:model-value="(open: boolean) => !open && (asking = null)"
    @confirm="confirmRemove"
  />
</template>

<script setup lang="ts">
// 「我的草稿」：我在这个空间还没发布的题目，和对已发布题目还没保存的修改。点名字接着写，
// 「删除」先问一句。只拿 props、只发事件；读和删在 MyDrafts.vue。
import type { NavTarget } from '@/lib/navTarget'
import type { TaskDraftSummary } from '@/types'

import { ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import BaseTable from '@/components/base/BaseTable.vue'
import ConfirmDialog from '@/components/base/ConfirmDialog.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import { relTime } from '@/lib/relTime'

const props = defineProps<{
  drafts: TaskDraftSummary[]
  loading: boolean
  failed: boolean
  publishTo: NavTarget
  /** 正在删的那一份；删完（或失败）外面置回 null。 */
  removing: boolean
}>()

const emit = defineEmits<{
  open: [draft: TaskDraftSummary]
  remove: [id: number]
  retry: []
}>()

const { t } = useI18n()

const asking = ref<TaskDraftSummary | null>(null)

function nameOf(draft: TaskDraftSummary): string {
  return draft.name || draft.taskName || t('spaces.detail.publishTask.drafts.untitled')
}

function confirmRemove() {
  if (asking.value) emit('remove', asking.value.id)
}

// 删完了就关掉那一问。
watch(
  () => props.removing,
  (now, before) => {
    if (before && !now) asking.value = null
  }
)
</script>

<style scoped>
.mdr {
  width: min(var(--page-w), 100%);
  padding: 8px 16px 48px;
  margin: 0 auto;
}

.mdr__open {
  display: inline-flex;
  gap: 8px;
  align-items: center;
  max-width: 100%;
  padding: 0;
  color: var(--ink);
  text-align: left;
}

.mdr__open:hover .mdr__name {
  text-decoration: underline;
}

.mdr__name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mdr__kind {
  flex-shrink: 0;
  padding: 0 6px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
  background: var(--fill);
  border-radius: var(--radius-sm);
}

.mdr__muted {
  color: var(--muted);
}

.mdr__sr {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
}
</style>
