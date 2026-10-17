<script setup lang="ts">
// 让 AI 队友起草项目目标之前，可以补充几句、加几份材料：两样都可以不给，直接起草。
// 材料由上一层放进资料库，AI 队友和以后的人都从那里读；这一层只收集和交出去。
import { computed, ref } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseField from '@/components/base/BaseField.vue'
import { t } from '@/i18n'

const props = defineProps<{
  agentName: string
  /** 正在把材料放进资料库、准备起草。 */
  busy: boolean
  /** 上一次没起成的原因。 */
  error: string | null
}>()

const emit = defineEmits<{
  start: [draft: { note: string; files: File[] }]
  cancel: []
}>()

/** 补充说明最多这么多字：连同材料的地址一起，要放得进一次提问（4000 字）。 */
const NOTE_MAX = 3000

const note = ref('')
const files = ref<File[]>([])
const picker = ref<HTMLInputElement | null>(null)
const noteLength = computed(() => note.value.length)

function pick(event: Event) {
  const input = event.target as HTMLInputElement
  const known = new Set(files.value.map((f) => f.name))
  files.value = [...files.value, ...Array.from(input.files ?? []).filter((f) => !known.has(f.name))]
  input.value = ''
}
function remove(file: File) {
  files.value = files.value.filter((f) => f !== file)
}
function start() {
  if (props.busy) return
  emit('start', { note: note.value.trim(), files: files.value })
}
</script>

<template>
  <div class="draft" data-testid="overview-draft-form">
    <BaseField
      :label="t('work.overview.draftNote')"
      optional
      :hint="t('work.overview.draftReads', { agent: agentName })"
      :counter="{ current: noteLength, max: NOTE_MAX }"
    >
      <template #default="{ id, describedby }">
        <v-textarea
          :id="id"
          v-model="note"
          :aria-describedby="describedby"
          :placeholder="t('work.overview.draftNotePlaceholder')"
          :maxlength="NOTE_MAX"
          variant="outlined"
          rows="3"
          auto-grow
          hide-details
          autocomplete="off"
        />
      </template>
    </BaseField>

    <div class="draft__files">
      <ul v-if="files.length" class="draft__list">
        <li v-for="file in files" :key="file.name" class="draft__file t-body">
          <span class="draft__name">{{ file.name }}</span>
          <BaseButton
            kind="ghost"
            size="sm"
            icon="mdi-close"
            :aria-label="t('work.overview.draftRemoveFile', { name: file.name })"
            @click="remove(file)"
          />
        </li>
      </ul>
      <input
        ref="picker"
        type="file"
        multiple
        class="draft__picker"
        tabindex="-1"
        :aria-label="t('work.overview.draftAddFiles')"
        @change="pick"
      />
      <BaseButton kind="ghost" size="sm" prepend-icon="mdi-paperclip" @click="picker?.click()">
        {{ t('work.overview.draftAddFiles') }}
      </BaseButton>
      <span class="t-meta c-muted">{{ t('work.overview.draftFilesHint') }}</span>
    </div>

    <p v-if="error" class="draft__error t-body" role="alert">{{ error }}</p>

    <div class="draft__actions">
      <BaseButton kind="primary" :loading="busy" data-testid="overview-draft-start" @click="start">
        {{ t('work.overview.draftStart') }}
      </BaseButton>
      <BaseButton kind="ghost" :disabled="busy" @click="emit('cancel')">
        {{ t('work.overview.draftCancel') }}
      </BaseButton>
    </div>
  </div>
</template>

<style scoped>
.draft {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 16px;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
}
.draft__files {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.draft__list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  width: 100%;
  margin: 0;
  padding: 0;
  list-style: none;
}
.draft__file {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--text);
}
.draft__name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.draft__picker {
  display: none;
}
.draft__error {
  margin: 0;
  color: var(--danger-ink);
}
.draft__actions {
  display: flex;
  gap: 8px;
}
</style>
