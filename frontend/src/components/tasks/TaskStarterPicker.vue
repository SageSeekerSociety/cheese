<script setup lang="ts">
// 题目的起始代码（#4767）：一份 .zip，领题后解开，作为项目代码仓库的第一个提交。
//
// 只画、只报：选了哪一份（`pick`，换一份也是它）、不要了（`remove`）。传到哪里由页面决定
// —— 发题时先传成附件、建题那条请求带上它的 id；改题时直接设成这道题的起始代码。压缩包
// 合不合格由服务端当场查，不合格的那句话由页面弹出来。改题页上移除当场生效、影响之后
// 每个开始做的人（`confirmRemove`），先问一句再报 `remove`。
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseField from '@/components/base/BaseField.vue'
import ConfirmDialog from '@/components/base/ConfirmDialog.vue'
import { fmtFileSize, fmtSizeLimit } from '@/lib/usageFormat'

const props = defineProps<{
  /** 现在的那一份；没有是 null。 */
  file: { name: string; size: number } | null
  /** 正在上传：按钮先按住，免得同时传两份。 */
  uploading: boolean
  /** 传到了百分之几；`null` / 不给 = 报不出来，只写「上传中」。传满了平台还在收，写「处理中」。 */
  percent?: number | null
  /** 移除前先问一句（改题页：移除当场生效）。 */
  confirmRemove?: boolean
  /** 这一页收得下多大的压缩包（发题页：附件的上限）；给了就写在说明里，并说更大的去改题页传。 */
  maxBytes?: number | null
}>()

const emit = defineEmits<{
  pick: [file: File]
  remove: []
}>()

const { t } = useI18n()

const input = ref<HTMLInputElement | null>(null)

const hint = computed(() =>
  props.maxBytes
    ? t('tasks.starterPicker.hintWithLimit', { size: fmtSizeLimit(props.maxBytes) })
    : t('tasks.starterPicker.hint')
)

const confirming = ref(false)

function askRemove() {
  if (props.confirmRemove) confirming.value = true
  else emit('remove')
}

function confirmRemoval() {
  confirming.value = false
  emit('remove')
}

function onPicked(event: Event) {
  const target = event.target as HTMLInputElement
  const [file] = Array.from(target.files ?? [])
  target.value = ''
  if (file) emit('pick', file)
}
</script>

<template>
  <BaseField :label="t('tasks.starterPicker.title')" :hint="hint">
    <div class="tsp" data-agent-field="starter">
      <div v-if="file" class="tsp__row" data-testid="starter-file">
        <v-icon size="16" class="tsp__icon">mdi-folder-zip-outline</v-icon>
        <span class="tsp__name">{{ file.name }}</span>
        <span class="tsp__size t-num">{{ fmtFileSize(file.size) }}</span>
        <BaseButton
          kind="ghost"
          size="sm"
          :disabled="uploading"
          :aria-label="t('tasks.starterPicker.replace')"
          @click="input?.click()"
        >
          {{ t('tasks.starterPicker.replaceShort') }}
        </BaseButton>
        <BaseButton
          kind="ghost"
          size="sm"
          :disabled="uploading"
          :aria-label="t('tasks.starterPicker.remove')"
          @click="askRemove"
        >
          {{ t('tasks.starterPicker.removeShort') }}
        </BaseButton>
      </div>
      <input
        ref="input"
        type="file"
        accept=".zip,application/zip"
        hidden
        data-testid="starter-input"
        :aria-label="t('tasks.starterPicker.pick')"
        @change="onPicked"
      />
      <button v-if="!file" type="button" class="tsp__add" :disabled="uploading" @click="input?.click()">
        <v-progress-circular v-if="uploading" indeterminate size="16" width="2" />
        <v-icon v-else size="18">mdi-plus</v-icon>
        {{
          !uploading
            ? t('tasks.starterPicker.pick')
            : percent == null
              ? t('tasks.starterPicker.uploading')
              : percent >= 100
                ? t('tasks.starterPicker.processing')
                : t('tasks.starterPicker.uploadingPercent', { percent })
        }}
      </button>
    </div>
    <ConfirmDialog
      v-model="confirming"
      :title="t('tasks.starterPicker.removeTitle')"
      :confirm-label="t('tasks.starterPicker.removeShort')"
      danger
      @confirm="confirmRemoval"
    >
      {{ t('tasks.starterPicker.removeBody') }}
    </ConfirmDialog>
  </BaseField>
</template>

<style scoped>
.tsp {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.tsp__row {
  display: flex;
  gap: 8px;
  align-items: center;
  min-height: 40px;
  padding: 0 4px 0 12px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
  font-size: 14px;
  line-height: var(--lh-14);
}

.tsp__icon {
  color: var(--muted);
}

.tsp__name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  color: var(--text);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tsp__size {
  color: var(--muted);
  font-size: 13px;
}

.tsp__add {
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

.tsp__add:hover:not(:disabled) {
  background: var(--fill);
}

.tsp__add:disabled {
  cursor: default;
  opacity: 0.6;
}
</style>
