<template>
  <div class="file-upload">
    <input
      ref="input"
      type="file"
      :accept="accept"
      :multiple="max > 1"
      :max="max"
      :disabled="disabled"
      @change="onFileChange"
    />
    <div :class="contentClass" @click="onButtonClick">
      <slot>
        <BaseButton kind="secondary" :disabled="disabled">
          <v-icon>mdi-upload</v-icon>
          {{ t('shell.fileSelect.upload') }}
        </BaseButton>
      </slot>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, toRefs } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

const input = ref<HTMLInputElement | null>(null)

const emit = defineEmits<{
  change: [files: Array<File>]
  error: [error: Error]
}>()

const files = defineModel<Array<File>>()
const props = withDefaults(
  defineProps<{
    accept?: string
    contentClass?: string
    max?: number
    disabled?: boolean
  }>(),
  {
    accept: () => '*/*',
    max: () => 1,
    disabled: false,
    contentClass: '',
  }
)
const { accept, max, disabled } = toRefs(props)

const onFileChange = (e: Event) => {
  const target = e.target as HTMLInputElement
  if (!target.files) return
  const newFiles = Array.from(target.files)
  if (newFiles.length > max.value) {
    emit('error', new Error(t('shell.fileSelect.tooMany', { max: max.value })))
    return
  }
  files.value = newFiles
  target.value = ''
  emit('change', newFiles)
}

const onButtonClick = () => {
  input.value?.click()
}
</script>

<style scoped lang="scss">
.file-upload {
  position: relative;
  display: inline-block;
  cursor: pointer;

  input {
    position: absolute;
    top: 0;
    left: 0;
    width: 100%;
    height: 100%;
    opacity: 0;
  }
}
</style>
