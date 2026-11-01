<script setup lang="ts">
// 发题 / 改题时这道题带的材料：已经挂上的那几份，加一个「添加文件」。
//
// 它只画、只报：选了哪些文件（`add`）、要去掉哪一份（`remove`）、要把哪一份换成新的一版
// （`replace`，`replaceable` 时才有）。传到哪里、什么时候传由页面决定 —— 发题时先传成
// 游离的附件、建题那条请求再带上它们的 id；改题时直接放进这道题的资料库。上限是接口报的
// 那个数（`GET /attachments/limits`），也由页面问来给它。
import type { PickedAttachment } from '@/composables/useAttachmentUploads'

import { ref } from 'vue'
import { useI18n } from 'vue-i18n'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseField from '@/components/base/BaseField.vue'
import { fmtFileSize, fmtSizeLimit } from '@/lib/usageFormat'

defineProps<{
  files: PickedAttachment[]
  /** 正在上传：「添加文件」先按住，免得同一份传两遍。 */
  uploading: boolean
  /** 正在传的那一份传到了百分之几；`null` / 不给 = 报不出来，只写「上传中」。传满了平台还在收，写「处理中」。 */
  percent?: number | null
  /** 单份文件的上限；`null` = 没问到，不写这句话。 */
  maxFileBytes: number | null
  /** 每一行能不能换成新的一版（改题时，旧的一版留作历史）。 */
  replaceable?: boolean
}>()

const emit = defineEmits<{
  add: [files: File[]]
  remove: [id: number | string]
  replace: [id: number | string, file: File]
}>()

const { t } = useI18n()

const input = ref<HTMLInputElement | null>(null)
const replacement = ref<HTMLInputElement | null>(null)
/** 正在为哪一份挑新的一版。 */
const replacing = ref<number | string | null>(null)

function onPicked(event: Event) {
  const target = event.target as HTMLInputElement
  const files = Array.from(target.files ?? [])
  target.value = ''
  if (files.length) emit('add', files)
}

function pickReplacement(id: number | string) {
  replacing.value = id
  replacement.value?.click()
}

function onReplacement(event: Event) {
  const target = event.target as HTMLInputElement
  const [file] = Array.from(target.files ?? [])
  target.value = ''
  if (file && replacing.value !== null) emit('replace', replacing.value, file)
  replacing.value = null
}
</script>

<template>
  <BaseField
    :label="t('tasks.attachmentPicker.title')"
    :hint="maxFileBytes ? t('tasks.attachmentPicker.limit', { size: fmtSizeLimit(maxFileBytes) }) : undefined"
  >
    <div class="tap">
      <ul v-if="files.length" class="tap__list">
        <li v-for="file in files" :key="file.id" class="tap__row" data-testid="attached-file">
          <v-icon size="16" class="tap__icon">mdi-file-outline</v-icon>
          <span class="tap__name">{{ file.name }}</span>
          <span class="tap__size t-num">{{ fmtFileSize(file.size) }}</span>
          <BaseButton
            v-if="replaceable"
            icon="mdi-file-replace-outline"
            size="sm"
            :disabled="uploading"
            :title="t('tasks.attachmentPicker.replace', { name: file.name })"
            :aria-label="t('tasks.attachmentPicker.replace', { name: file.name })"
            @click="pickReplacement(file.id)"
          />
          <BaseButton
            icon="mdi-close"
            size="sm"
            :disabled="uploading"
            :title="t('tasks.attachmentPicker.remove', { name: file.name })"
            :aria-label="t('tasks.attachmentPicker.remove', { name: file.name })"
            @click="emit('remove', file.id)"
          />
        </li>
      </ul>
      <input
        v-if="replaceable"
        ref="replacement"
        type="file"
        hidden
        data-testid="attachment-replacement"
        :aria-label="t('tasks.attachmentPicker.replaceWith')"
        @change="onReplacement"
      />
      <input
        ref="input"
        type="file"
        multiple
        hidden
        data-testid="attachment-input"
        :aria-label="t('tasks.attachmentPicker.add')"
        @change="onPicked"
      />
      <button type="button" class="tap__add" :disabled="uploading" @click="input?.click()">
        <v-progress-circular v-if="uploading" indeterminate size="16" width="2" />
        <v-icon v-else size="18">mdi-plus</v-icon>
        {{
          !uploading
            ? t('tasks.attachmentPicker.add')
            : percent == null
              ? t('tasks.attachmentPicker.uploading')
              : percent >= 100
                ? t('tasks.attachmentPicker.processing')
                : t('tasks.attachmentPicker.uploadingPercent', { percent })
        }}
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
