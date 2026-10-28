<script setup lang="ts">
// 图片那一档：房间里指定一份图片文件，就把它铺在这一格里。
//
// 图上能画圈、写一句话、把合成好的那张图连同那句话交给房间（`DesignImage` 那一半，
// 这里只把它的动作接回来）。「指到哪里」的判据和状态都在 `usePreviewLocator` 那一份里：
// 这一档拿到的是它算好的结果，自己不知道该显示什么、发得出去发不出去。
//
// 框选之后那张写批注的卡由宿主（`PanelPreviewView`）的统一「指一处」卡负责，和别的
// 文件类型同一张——这一档不再自造第二张。
import type { ImageRegionMark, RasterSelection } from './designRegion'
import type { ImageRegionHandle } from './usePreviewImageRegion'

import DesignImage from './DesignImage.vue'

import { t } from '@/i18n'

defineProps<{
  documentName: string
  active?: boolean
  /** 全屏里才把滚轮让给缩放：面板里这张图嵌在一段要滚的正文里。 */
  previewFull: boolean
  imageRegion: ImageRegionHandle
  docError: string
  annotateError: string
  /** 图上框出来的那几处评论（原图像素），由宿主按屏幕上那一版筛好。 */
  marks?: readonly ImageRegionMark[]
  /** 打开着的那一串，画得实一点。 */
  activeMark?: string | null
}>()

const emit = defineEmits<{
  region: [selection: RasterSelection]
  annotate: [
    payload: { blob: Blob; filename: string; naturalWidth: number; naturalHeight: number; count: number; note: string },
  ]
  /** 点了一处评论标记：把那一串打开。 */
  mark: [id: string]
}>()
</script>

<template>
  <div class="file-image">
    <DesignImage
      v-if="imageRegion.imageSrc.value"
      :src="imageRegion.imageSrc.value"
      :alt="documentName"
      :identity="imageRegion.imageIdentity.value"
      :selection-enabled="imageRegion.selectionEnabled.value"
      :active-region="imageRegion.target.value?.selection.region ?? null"
      :active="active"
      :zoom-on-wheel="previewFull"
      :marks="marks"
      :active-mark="activeMark"
      @region="emit('region', $event)"
      @annotate="emit('annotate', $event)"
      @mark="emit('mark', $event)"
    >
      <template #actions
        ><a class="image-open" :href="imageRegion.imageSrc.value" target="_blank" rel="noopener">{{
          t('work.room.preview.openInNewWindow')
        }}</a></template
      >
    </DesignImage>
    <div v-else class="doc__state" role="status">
      <span v-if="docError">{{ docError }}</span>
      <v-progress-circular v-else indeterminate color="primary" size="24" />
    </div>
    <p v-if="annotateError" class="file-image__error" role="alert">{{ annotateError }}</p>
  </div>
</template>

<style scoped src="./stageState.css"></style>

<style scoped>
.file-image {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  min-width: 0;
  min-height: var(--preview-min);
}
.file-image__error {
  padding: 6px 8px;
  color: var(--danger-ink);
  font-size: 13px;
  line-height: var(--lh-13);
}
.image-open {
  padding: 4px 8px;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
  text-decoration: none;
  border-radius: var(--radius-sm);
}
.image-open:hover {
  background: var(--fill-2);
}
.image-open:focus-visible {
  outline: 2px solid var(--accent);
}
</style>
