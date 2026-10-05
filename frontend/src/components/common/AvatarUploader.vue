<!--
  取景蒙版（下面的 .uploader：暗底 + 白色虚线框）是**有意保留**写死的 rgba 的，
  别换成语义 token —— 见 docs/design-system.md §1.2 的例外条款：它和相机 App 的
  取景框一样，两套主题下同一个样子，换成语义色反而会让某一套下的取景框露馅。

  占位底和提示文字则按 §1.2 的映射表走语义 token：占位底取 surface-light（填充块），
  提示文字取 on-surface；它们叠在照片上，跟着主题各取一档，不会在某一套下字看不清。
-->
<template>
  <div class="avatar-upload" :class="{ 'avatar-upload--empty': !avatarFile }">
    <v-img
      :src="previewUrl || src || undefined"
      aspect-ratio="1"
      class="rounded-lg avatar"
      rounded="0"
      size="180"
      color="surface-light"
    />
    <file-select
      v-model="files"
      accept="image/*"
      :max="1"
      :disabled="disabled"
      class="uploader"
      content-class="uploader-inner"
      @error="onError"
    >
      <div class="rounded-lg d-flex flex-column align-center justify-center gap-4 pa-4 text-on-surface uploader-inner">
        <v-icon size="32">mdi-camera</v-icon>
        <div class="text-body-1 text-on-surface">
          {{ avatarFile || src ? t('shell.avatar.change') : t('shell.avatar.upload') }}
        </div>
      </div>
    </file-select>
  </div>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'

import FileSelect from './FileSelect.vue'

import { t } from '@/i18n'

const emit = defineEmits<{
  (e: 'error', error: Error): void
}>()

const files = ref<File[]>([])
defineProps<{ src?: string; disabled?: boolean }>()
const avatarFile = defineModel<File>()
const previewUrl = ref<string | null>(null)

watch(files, (newFiles) => {
  if (newFiles && newFiles.length > 0) {
    const file = newFiles[0]
    previewUrl.value = URL.createObjectURL(file)
    avatarFile.value = file
  } else {
    previewUrl.value = null
    avatarFile.value = undefined
  }
})

const onError = (error: Error) => {
  emit('error', error)
}
</script>

<style scoped lang="scss">
.avatar-upload {
  position: relative;

  &:not(.avatar-upload--empty):hover {
    .uploader {
      opacity: 1;
    }
  }

  &.avatar-upload--empty {
    .uploader {
      opacity: 1;
    }
  }

  .uploader {
    position: absolute;
    opacity: 0;
    transition: opacity 0.2s ease-in-out;
    top: 0;
    left: 0;
    bottom: 0;
    right: 0;
    /* 同上：这两个值构成蒙版本身（暗底 + 白色虚线框），是主题无关的取景框，不 token 化 */
    border: 2px dashed rgba(255, 255, 255, 0.5);
    border-radius: 8px;
    background-color: rgba(0, 0, 0, 0.25);
  }
}
</style>

<style lang="scss">
.uploader-inner {
  height: 100%;
}
</style>
