<!--
  这个控件的三处固定调色板名（占位底的固定深灰 + 两处白字工具类）是**有意保留**的，
  别换成语义 token —— 见 docs/design-system.md §1.2 的例外条款：
  底色本身不随主题变的地方，压在上面的前景色也不该变。

  它整体是「一张照片 + 一层暗色蒙版 + 白色提示文字」，和相机 App 的取景蒙版一样，
  两套主题下都长这样。占位底 grey-darken-1(#757575) 是没传头像时垫在蒙版下面的那层，
  它必须够暗，白字才读得出来：#757575 上叠 25% 黑得到 #585858，白字 6.8:1。
  换成 surface-variant（浅色 #EEEFF1）的话，白字会掉到约 1.9:1，浅色主题当场就坏了。
-->
<template>
  <div class="avatar-upload" :class="{ 'avatar-upload--empty': !avatarFile }">
    <v-img
      :src="previewUrl || undefined"
      aspect-ratio="1"
      class="rounded-lg avatar"
      rounded="0"
      size="180"
      color="grey-darken-1"
    />
    <file-select
      v-model="files"
      accept="image/*"
      :max="1"
      class="uploader"
      content-class="uploader-inner"
      @error="onError"
    >
      <div class="rounded-lg d-flex flex-column align-center justify-center gap-4 pa-4 text-white uploader-inner">
        <v-icon size="32">mdi-camera</v-icon>
        <div class="text-body-1 text-white">{{ avatarFile ? '更换头像' : '上传头像' }}</div>
      </div>
    </file-select>
  </div>
</template>

<script setup lang="ts">
import { ref, toRefs, watch } from 'vue'

import FileSelect from './FileSelect.vue'

const emit = defineEmits<{
  (e: 'error', error: Error): void
}>()

const files = ref<File[]>([])
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
