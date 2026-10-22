<script setup lang="ts">
// 提交反馈表单里的那一栏附件：已经选好的几行，加一个「添加图片或文件」。
//
// 它只画、只报（选了哪些、去掉哪一份、重试哪一份）；上传、名单和落盘都在
// `useFeedbackAttachments` 里 —— 和发题目那一栏（`TaskAttachmentPicker`）同一个分工，
// 理由也一样：上传时机是**页面**的决定（选完就传），而这一格只该知道怎么画。
//
// **粘贴不由这一格听**：粘贴要在整个表单里都有效（人在标题框里刚截完图，不该先点回
// 这一栏），所以 `paste` 挂在表单上，文件转给这里的 `add`。这一格只留一个提示句告诉
// 人有这回事。
import type { PendingAttachment } from '@/composables/useFeedbackAttachments'

import { ref } from 'vue'
import { useI18n } from 'vue-i18n'

import BaseButton from '@/components/base/BaseButton.vue'
import { fmtFileSize } from '@/lib/usageFormat'

defineProps<{
  rows: PendingAttachment[]
  /** 单个文件的上限；用来写那行说明。 */
  maxBytes: number
  maxFiles: number
}>()

const emit = defineEmits<{
  add: [files: File[]]
  remove: [key: string]
  retry: [key: string]
}>()

const { t } = useI18n()
const input = ref<HTMLInputElement | null>(null)

function onPicked(event: Event) {
  const target = event.target as HTMLInputElement
  const files = Array.from(target.files ?? [])
  // 清掉，好让同一个文件再选一次也会触发 change。
  target.value = ''
  if (files.length) emit('add', files)
}
</script>

<template>
  <div class="sba">
    <div class="sba__label">{{ t('feedback.submit.attachments.add') }}</div>
    <p class="sba__hint t-meta-read">
      {{ t('feedback.submit.attachments.helper') }} ·
      {{ t('feedback.submit.attachments.limit', { size: fmtFileSize(maxBytes), max: maxFiles }) }}
    </p>

    <ul v-if="rows.length" class="sba__list">
      <li v-for="row in rows" :key="row.key" class="sba__row" data-testid="feedback-attachment">
        <v-icon size="16" class="sba__icon" aria-hidden="true">
          {{ row.state === 'failed' ? 'mdi-alert-circle-outline' : 'mdi-file-outline' }}
        </v-icon>
        <span class="sba__name">{{ row.name }}</span>

        <!-- 正在传：进度条画在名字后面那一条，不占一行。进度条自己不带字，所以它
             的可访问名字由这一格给 —— 读屏读到的正是页面上看不见的那句「传到哪了」。 -->
        <span
          v-if="row.state === 'uploading'"
          class="sba__bar"
          role="progressbar"
          :aria-label="
            t('feedback.submit.attachments.uploading', { name: row.name, percent: Math.round(row.progress * 100) })
          "
          :aria-valuenow="Math.round(row.progress * 100)"
        >
          <span class="sba__bar-fill" :style="{ width: `${Math.round(row.progress * 100)}%` }" />
        </span>
        <span v-else-if="row.state === 'failed'" class="sba__error">{{ row.error }}</span>
        <span v-else class="sba__size t-num">{{ fmtFileSize(row.bytes) }}</span>

        <BaseButton v-if="row.state === 'failed'" kind="ghost" size="sm" @click="emit('retry', row.key)">
          {{ t('feedback.submit.attachments.retry') }}
        </BaseButton>
        <BaseButton
          icon="mdi-close"
          size="sm"
          :aria-label="t('feedback.submit.attachments.remove', { name: row.name })"
          @click="emit('remove', row.key)"
        />
      </li>
    </ul>

    <input
      ref="input"
      type="file"
      multiple
      hidden
      data-testid="feedback-attachment-input"
      :aria-label="t('feedback.submit.attachments.add')"
      @change="onPicked"
    />
    <button type="button" class="sba__add" @click="input?.click()">
      <v-icon size="18" aria-hidden="true">mdi-paperclip</v-icon>
      {{ t('feedback.submit.attachments.add') }}
    </button>
  </div>
</template>

<style scoped>
.sba__label {
  font-size: 13px;
  font-weight: 600;
  line-height: var(--lh-13);
  color: var(--ink);
}
.sba__hint {
  margin: 4px 0 0;
}
.sba__list {
  margin: 10px 0 0;
  padding: 0;
  list-style: none;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
}
.sba__row {
  display: flex;
  gap: 8px;
  align-items: center;
  min-height: 40px;
  padding: 0 4px 0 12px;
  font-size: 14px;
  line-height: var(--lh-14);
}
.sba__row + .sba__row {
  border-top: 1px solid var(--line);
}
.sba__icon {
  flex: none;
  color: var(--muted);
}
.sba__name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  color: var(--text);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.sba__size {
  flex: none;
  color: var(--muted);
  font-size: 12px;
}
/* 上传中那一条：一小段槽 + 一段填充。用 --fill / --accent（**不是**状态色）：上传
   中的不是一件事的结果，它还没结果。 */
.sba__bar {
  flex: none;
  width: 72px;
  height: 4px;
  overflow: hidden;
  background: var(--fill);
  border-radius: 2px;
}
.sba__bar-fill {
  display: block;
  height: 100%;
  background: var(--accent);
  transition: width 0.15s ease;
}
/* 失败的那一行：原因用 --danger-ink（文字那一档，不是 --danger 那个标记色）。 */
.sba__error {
  flex: none;
  max-width: 45%;
  overflow: hidden;
  color: var(--danger-ink);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
/* 「添加图片或文件」：和这一页别的次操作同高同圆角，但走一条虚线边 —— 它是这一栏的
   唯一的动作，虚线说明「这里还能往里放东西」。 */
.sba__add {
  display: flex;
  gap: 6px;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 40px;
  margin-top: 10px;
  color: var(--muted);
  font-family: inherit;
  font-size: 13px;
  background: transparent;
  border: 1px dashed var(--line-2);
  border-radius: var(--radius-md);
  cursor: pointer;
  transition:
    color 0.12s ease,
    border-color 0.12s ease;
}
.sba__add:hover {
  color: var(--text);
  border-color: var(--line);
}
</style>
