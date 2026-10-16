<!--
  这个控件整体是「一张照片 + 一层蒙版 + 压在上面的浅色提示文字」，和相机 App 的取景蒙版一样。

  它走的是 §2.5 的反色块，而不是 --surface 那一套语义色 —— 白字压在 --fill-2（浅色下
  #EEEFF1）上只有约 1.2:1，当场就坏。底用 --inverse-surface，字用 --inverse-ink：浅色下
  是一块比页面暗的底，深色下是一块比页面亮的灰（§2.5），字在两套主题下都是浅色的。
  注意这一对是随主题变的，所以这里不适用 §1.2 的例外——那条是给底色不跟主题变的地方用的。
-->
<template>
  <div class="avatar-upload" :class="{ 'avatar-upload--empty': !avatarFile }">
    <v-img :src="previewUrl || src || undefined" aspect-ratio="1" class="rounded-lg avatar" rounded="0" size="180" />
    <file-select
      v-model="files"
      accept="image/*"
      :max="1"
      :disabled="disabled"
      class="uploader"
      content-class="uploader-inner"
      @error="onError"
    >
      <div class="rounded-lg d-flex flex-column align-center justify-center gap-4 pa-4 uploader-inner">
        <v-icon size="32">mdi-camera</v-icon>
        <div class="text-body-1">
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

  /* 还没传头像时垫在蒙版底下的那一层：一块反色方块（§2.5），两套主题下都够暗，
     浅色字才读得出来。 */
  .avatar {
    background: var(--inverse-surface);
  }

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
    inset: 0;
    opacity: 0;
    transition: opacity var(--dur-quick) var(--ease-standard);
    /* 蒙版本身：压在照片上的一层反色块（§2.5）+ 浅色虚线框，和相机 App 的取景蒙版一样。 */
    border: 2px dashed color-mix(in srgb, var(--inverse-ink) 50%, transparent);
    border-radius: var(--radius-md);
    background-color: color-mix(in srgb, var(--inverse-surface) 25%, transparent);
  }
}
</style>

<style lang="scss">
.uploader-inner {
  height: 100%;
  /* 压在蒙版上的字和图标：和蒙版同一族的 --inverse-ink，两套主题下都是浅色。 */
  color: var(--inverse-ink);
}
</style>
