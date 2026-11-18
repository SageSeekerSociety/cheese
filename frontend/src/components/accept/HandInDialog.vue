<script setup lang="ts">
// 人自己提交审阅的小面板：说明（标题一行，补充说明可不写）、这次会交进去的文件和它们
// 增删的行数、交给谁审阅，然后取消或提交。审阅的就是自己时，主按钮是「提交并采纳」。
// 说明打开时由平台照改动起草，起草中标题那一格先空着、写着「正在起草」，人可以直接写。
import type { HandInFile } from '@/api'

import BaseField from '@/components/base/BaseField.vue'
import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import { t } from '@/i18n'

defineOptions({ name: 'HandInDialog' })

const open = defineModel<boolean>({ default: false })

withDefaults(
  defineProps<{
    /** 有一版在等审阅：这是再提交一版。 */
    again?: boolean
    /** 审阅的是自己：提交了就采纳。 */
    adopts?: boolean
    /** 这次会交进去的文件；还没读回来是 null。 */
    files?: HandInFile[] | null
    subject: string
    body: string
    reviewer: string | null
    /** 能审阅的人。 */
    reviewers: { handle: string; name: string }[]
    drafting?: boolean
    submitting?: boolean
    ready?: boolean
  }>(),
  { again: false, adopts: false, files: null, drafting: false, submitting: false, ready: false }
)

const emit = defineEmits<{
  (e: 'update:subject', value: string): void
  (e: 'update:body', value: string): void
  (e: 'update:reviewer', value: string | null): void
  (e: 'submit'): void
}>()
</script>

<template>
  <AdaptiveDialog
    v-model="open"
    size="sm"
    :title="again ? t('work.room.accept.handIn.resubmit') : t('work.room.accept.handIn.submit')"
    :primary-label="adopts ? t('work.room.accept.handIn.submitAndAdopt') : t('work.room.accept.handIn.submit')"
    :primary-loading="submitting"
    :primary-disabled="!ready"
    :close-disabled="submitting"
    @primary="emit('submit')"
  >
    <div class="hand-in">
      <BaseField :label="t('work.room.accept.handIn.note')">
        <template #default="{ id }">
          <v-text-field
            :id="id"
            :model-value="subject"
            autocomplete="off"
            variant="outlined"
            density="comfortable"
            hide-details
            :placeholder="drafting ? t('work.room.accept.handIn.drafting') : undefined"
            :loading="drafting"
            data-testid="hand-in-subject"
            @update:model-value="emit('update:subject', $event ?? '')"
          />
          <v-textarea
            class="hand-in__body"
            autocomplete="off"
            :model-value="body"
            :aria-label="t('work.room.accept.handIn.body')"
            :placeholder="drafting ? t('work.room.accept.handIn.drafting') : t('work.room.accept.handIn.body')"
            variant="outlined"
            density="comfortable"
            rows="3"
            auto-grow
            hide-details
            data-testid="hand-in-body"
            @update:model-value="emit('update:body', $event ?? '')"
          />
        </template>
      </BaseField>

      <div class="hand-in__files" :aria-label="t('work.room.accept.handIn.files')">
        <v-progress-linear v-if="files === null" indeterminate />
        <span v-for="file in files ?? []" :key="file.path" class="hand-in__file t-meta">
          <span class="hand-in__path" :title="file.path">{{ file.path }}</span>
          <span class="hand-in__add">+{{ file.additions }}</span>
          <span class="hand-in__del">−{{ file.deletions }}</span>
        </span>
      </div>

      <BaseField :label="t('work.room.accept.handIn.reviewer')">
        <template #default="{ id }">
          <v-select
            :id="id"
            autocomplete="off"
            :model-value="reviewer"
            :items="reviewers"
            item-title="name"
            item-value="handle"
            variant="outlined"
            density="comfortable"
            hide-details
            data-testid="hand-in-reviewer"
            @update:model-value="emit('update:reviewer', $event ?? null)"
          />
        </template>
      </BaseField>
    </div>
  </AdaptiveDialog>
</template>

<style scoped>
.hand-in {
  display: grid;
  gap: 16px;
}
.hand-in__body {
  margin-top: 8px;
}
.hand-in__files {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.hand-in__file {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  max-width: 100%;
  padding: 1px 6px;
  border-radius: var(--radius-sm);
  background: var(--fill);
  color: var(--text);
  font-family: var(--font-mono);
}
.hand-in__path {
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.hand-in__add {
  color: var(--ok-ink);
}
.hand-in__del {
  color: var(--danger-ink);
}
</style>
