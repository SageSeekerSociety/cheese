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

import { computed } from 'vue'

import DesignImage from './DesignImage.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'
import { imageMayHaveAlpha } from '@/lib/fileKind'

const props = defineProps<{
  documentName: string
  /** 这一份的后缀：PNG / WebP / GIF 底下铺棋盘格，看出哪儿是透明。 */
  documentSuffix?: string
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

/** 这种格式可能带透明：底下铺棋盘格，透明的地方看得出是透明。 */
const transparent = computed(() => imageMayHaveAlpha(props.documentSuffix ?? ''))

const emit = defineEmits<{
  region: [selection: RasterSelection]
  annotate: [
    payload: { blob: Blob; filename: string; naturalWidth: number; naturalHeight: number; count: number; note: string },
  ]
  /** 点了一处评论标记：把那一串打开。 */
  mark: [id: string]
  /** 读不成的图上「重试」：请宿主重新读一遍这一份。 */
  retry: []
  /** 读不成时「下载原文件」。 */
  download: []
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
      :transparent="transparent"
      :gif-bytes="imageRegion.snapshot.value?.bytes ?? null"
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
    <!-- 字节还没到、或者没读成。读不成时别只留一行字：给三个出口，读者才知道下一步
         能干什么（重试 / 拿原文件 / 另开一页看）。 -->
    <div v-else class="doc__state doc__state--text" role="status">
      <template v-if="docError">
        <v-icon size="32" class="mb-2">mdi-image-broken-variant</v-icon>
        <div class="text-medium-emphasis">{{ t('work.room.preview.imageFailed') }}</div>
        <div class="text-caption mb-3">{{ docError }}</div>
        <div class="file-image__actions">
          <BaseButton kind="secondary" size="sm" prepend-icon="mdi-refresh" @click="emit('retry')">
            {{ t('work.room.preview.retry') }}
          </BaseButton>
          <BaseButton kind="secondary" size="sm" prepend-icon="mdi-download" @click="emit('download')">
            {{ t('work.room.preview.download') }}
          </BaseButton>
        </div>
      </template>
      <template v-else>
        <v-progress-circular indeterminate color="primary" size="24" />
        <div class="text-caption mt-2">{{ t('work.room.preview.imageReading') }}</div>
      </template>
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
.file-image__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  justify-content: center;
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
