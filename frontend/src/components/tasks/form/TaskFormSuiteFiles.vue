<script setup lang="ts">
// 测试点带着的一组文件：测试脚本的测试文件、命令判分的判分文件和保护文件（方案 §测试脚本、
// §命令判分）。
//
// 两种用法：
// - 上传（默认）：点「上传文件」一次选几份，路径默认是文件名，可以改成 `tests/test_x.py`
//   这样带目录的；
// - 保护文件（`perRow`）：出题人先打路径（起始代码里那几份），再给每一行传上它的原文 ——
//   后端存的是出题人这一刻给的字节，评测前用它覆盖学生的版本。没传原文的那一行标出来。
//
// 文件在浏览器里读：能按 UTF-8 读的送文本，读不了的送 base64（`readTestFile`）。它只画，
// 改一次往上报一次整组。
import type { TaskTestFile } from '@/types'

import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import BaseButton from '@/components/base/BaseButton.vue'
import { hasBytes } from '@/lib/taskTestSuite'
import { readTestFile } from '@/lib/testCaseZip'
import { fmtFileSize } from '@/lib/usageFormat'

const props = withDefaults(
  defineProps<{
    files: TaskTestFile[]
    label: string
    hint?: string
    /** 保护文件：先打路径，每一行再传原文。 */
    perRow?: boolean
    testid: string
  }>(),
  { hint: '', perRow: false }
)

const emit = defineEmits<{ 'update:files': [files: TaskTestFile[]] }>()

const { t } = useI18n()

const picker = ref<HTMLInputElement | null>(null)
/** 保护文件那一行在等哪一行的原文。 */
const rowAt = ref<number | null>(null)

const sizeOf = (file: TaskTestFile) =>
  file.bytes ??
  (file.content != null
    ? new TextEncoder().encode(file.content).length
    : file.base64 != null
      ? Math.floor((file.base64.length * 3) / 4)
      : null)

const total = computed(() => props.files.reduce((sum, file) => sum + (sizeOf(file) ?? 0), 0))

function setPath(index: number, path: string) {
  emit(
    'update:files',
    props.files.map((file, i) => (i === index ? { ...file, path } : file))
  )
}

function remove(index: number) {
  emit(
    'update:files',
    props.files.filter((_, i) => i !== index)
  )
}

function addPath() {
  emit('update:files', [...props.files, { path: '' }])
}

function pick(index: number | null) {
  rowAt.value = index
  picker.value?.click()
}

async function onPicked(event: Event) {
  const input = event.target as HTMLInputElement
  const picked = [...(input.files ?? [])]
  input.value = ''
  if (!picked.length) return
  const at = rowAt.value
  if (at != null) {
    // 保护文件的一行：路径是出题人打的，没打就用文件名。
    const current = props.files[at]
    if (!current) return
    const read = await readTestFile(picked[0]!, current.path.trim() || picked[0]!.name)
    emit(
      'update:files',
      props.files.map((file, i) => (i === at ? read : file))
    )
    return
  }
  const read = await Promise.all(picked.map((file) => readTestFile(file)))
  // 同名的新一份换掉旧的那一份，不重复加。
  const names = new Set(read.map((file) => file.path))
  emit('update:files', [...props.files.filter((file) => !names.has(file.path)), ...read])
}
</script>

<template>
  <div class="tfsf" :data-testid="testid">
    <div class="tfsf__head">
      <span class="tfsf__label">{{ label }}</span>
      <span v-if="files.length" class="t-meta-read t-num">{{
        t('tasks.judge.files.summary', { n: files.length, size: fmtFileSize(total, 1) })
      }}</span>
    </div>
    <p v-if="hint" class="tfsf__hint t-meta-read">{{ hint }}</p>
    <ul v-if="files.length" class="tfsf__list">
      <li v-for="(file, index) in files" :key="index" class="tfsf__row">
        <v-text-field
          :model-value="file.path"
          :aria-label="t('tasks.judge.files.path', { n: index + 1 })"
          :placeholder="perRow ? t('tasks.judge.files.protectedPlaceholder') : ''"
          autocomplete="off"
          maxlength="255"
          variant="outlined"
          density="compact"
          hide-details
          class="tfsf__path"
          :data-testid="`${testid}-path-${index}`"
          @update:model-value="setPath(index, String($event ?? ''))"
        />
        <span v-if="hasBytes(file)" class="tfsf__size t-meta-read t-num">{{ fmtFileSize(sizeOf(file) ?? 0, 1) }}</span>
        <span v-else class="tfsf__missing" :data-testid="`${testid}-missing-${index}`">{{
          t('tasks.judge.files.noContent')
        }}</span>
        <BaseButton
          v-if="perRow"
          kind="ghost"
          size="sm"
          icon="mdi-upload-outline"
          :title="t('tasks.judge.files.uploadOriginal', { n: index + 1 })"
          :aria-label="t('tasks.judge.files.uploadOriginal', { n: index + 1 })"
          :data-testid="`${testid}-upload-${index}`"
          @click="pick(index)"
        />
        <BaseButton
          kind="ghost"
          size="sm"
          icon="mdi-close"
          :title="t('tasks.judge.files.remove', { n: index + 1 })"
          :aria-label="t('tasks.judge.files.remove', { n: index + 1 })"
          @click="remove(index)"
        />
      </li>
    </ul>
    <div class="tfsf__foot">
      <BaseButton
        v-if="perRow"
        kind="secondary"
        size="sm"
        prepend-icon="mdi-plus"
        :data-testid="`${testid}-add`"
        @click="addPath"
        >{{ t('tasks.judge.files.addProtected') }}</BaseButton
      >
      <BaseButton
        v-else
        kind="secondary"
        size="sm"
        prepend-icon="mdi-upload-outline"
        :data-testid="`${testid}-add`"
        @click="pick(null)"
        >{{ t('tasks.judge.files.upload') }}</BaseButton
      >
    </div>
    <input
      ref="picker"
      type="file"
      :multiple="!perRow"
      hidden
      :aria-label="label"
      :data-testid="`${testid}-input`"
      @change="onPicked"
    />
  </div>
</template>

<style scoped>
.tfsf {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.tfsf__head {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.tfsf__label {
  color: var(--ink);
  font-size: 13px;
  font-weight: 500;
}

.tfsf__hint {
  margin: 0;
}

.tfsf__list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.tfsf__row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto auto auto;
  gap: 8px;
  align-items: center;
}

.tfsf__path :deep(input) {
  font-family: var(--font-mono);
  font-size: 13px;
}

.tfsf__size {
  min-width: 56px;
  text-align: right;
}

.tfsf__missing {
  color: var(--warn-ink);
  font-size: 12px;
}

.tfsf__foot {
  display: flex;
  gap: 8px;
}
</style>
