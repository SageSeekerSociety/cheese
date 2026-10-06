<script setup lang="ts">
// 发题 / 改题时这道题带的材料：已经挂上的那几份，加一个「添加文件」。
//
// 它只画、只报：选了哪些文件（`add`）、要去掉哪一份（`remove`）。传到哪里、什么时候
// 传由页面决定 —— 发题时先传成游离的附件、建题那条请求再带上它们的 id；改题时直接传到
// 这道题上。上限是接口报的那个数（`GET /attachments/limits`），也由页面问来给它。
import type { PickedAttachment } from '@/composables/useAttachmentUploads'

import { ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { formatFileSize } from '@/utils/materials'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseField from '@/components/base/BaseField.vue'

defineProps<{
  files: PickedAttachment[]
  /** 正在上传：「添加文件」先按住，免得同一份传两遍。 */
  uploading: boolean
  /** 单份文件的上限；`null` = 没问到，不写这句话。 */
  maxFileBytes: number | null
}>()

const emit = defineEmits<{
  add: [files: File[]]
  remove: [id: number]
}>()

const { t } = useI18n()

const input = ref<HTMLInputElement | null>(null)

function onPicked(event: Event) {
  const target = event.target as HTMLInputElement
  const files = Array.from(target.files ?? [])
  target.value = ''
  if (files.length) emit('add', files)
}
</script>

<template>
  <BaseField
    :label="t('tasks.attachmentPicker.title')"
    :hint="maxFileBytes ? t('tasks.attachmentPicker.limit', { size: formatFileSize(maxFileBytes) }) : undefined"
  >
    <div class="tap">
      <ul v-if="files.length" class="tap__list">
        <li v-for="file in files" :key="file.id" class="tap__row" data-testid="attached-file">
          <v-icon size="16" class="tap__icon">mdi-file-outline</v-icon>
          <span class="tap__name">{{ file.name }}</span>
          <span class="tap__size t-num">{{ formatFileSize(file.size) }}</span>
          <BaseButton
            icon="mdi-close"
            size="sm"
            :disabled="uploading"
            :aria-label="t('tasks.attachmentPicker.remove', { name: file.name })"
            @click="emit('remove', file.id)"
          />
        </li>
      </ul>
      <input ref="input" type="file" multiple hidden data-testid="attachment-input" @change="onPicked" />
      <button type="button" class="tap__add" :disabled="uploading" @click="input?.click()">
        <v-progress-circular v-if="uploading" indeterminate size="16" width="2" />
        <v-icon v-else size="18">mdi-plus</v-icon>
        {{ uploading ? t('tasks.attachmentPicker.uploading') : t('tasks.attachmentPicker.add') }}
      </button>
    </div>
  </BaseField>
</template>

<style scoped>
.tap {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.tap__list {
  margin: 0;
  padding: 0;
  list-style: none;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
}

.tap__row {
  display: flex;
  gap: 8px;
  align-items: center;
  min-height: 40px;
  padding: 0 4px 0 12px;
  font-size: 14px;
  line-height: var(--lh-14);
}

.tap__row + .tap__row {
  border-top: 1px solid var(--line);
}

.tap__icon {
  color: var(--muted);
}

.tap__name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  color: var(--text);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tap__size {
  color: var(--faint);
  font-size: 12px;
}

.tap__add {
  display: flex;
  gap: 8px;
  align-items: center;
  justify-content: center;
  height: 48px;
  border: 1px dashed var(--line-2);
  border-radius: var(--radius-md);
  background: none;
  color: var(--muted);
  font: inherit;
  font-size: 14px;
  cursor: pointer;
  transition: background-color var(--dur-quick) var(--ease-standard);
}

.tap__add:hover:not(:disabled) {
  background: var(--fill);
}

.tap__add:disabled {
  cursor: default;
  opacity: 0.6;
}
</style>
