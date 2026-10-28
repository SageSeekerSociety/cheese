<script setup lang="ts">
// 预览那一格的顶栏：发布、在新标签页打开、全屏、重读。
//
// 这一条管的都是「当前预览」。指定了文件的那一格（自由区的一个页签）没有它——那一点不
// 跟着当前预览走，它自己的两条画在下面的预览条上（见 `PreviewFrameStage`）。
//
// 全屏那一下不在这里收：这个 DOM 节点就是全屏的目标，宿主拿着它（`useFullscreen`），
// 关掉全屏的那条路也归宿主（帧里的 ESC，见 `usePreviewEscape`）。
//
// 图片也带全屏按钮（`isImageArtifact`）：图片正是那种「放大才画得准」的东西，而滚轮缩放
// 只在全屏里开着（见 `DesignImage` 的 `zoomOnWheel`）。
//
// 「发布」是项目级的事，落点是项目总览「做出了什么」最上面那块「网站」——在房间里看着一份
// 页面想把它发出去，这是唯一要跳出去的一下。
import type { PreviewFrame } from '@/composables/usePreviewFrames'
import type { FileContent } from '@/cx_types'

import { openPreviewInNewTab } from './previewNewTab'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

const props = defineProps<{
  topicId: string | null
  projectId: string | null
  previewUrl: string | null
  previewFile: FileContent | null
  displayedFrame?: PreviewFrame | null
  fullscreenSupported: boolean
  isImageArtifact: boolean
  previewFull: boolean
  refreshing: boolean
  /** 网页/应用那一档自己的小浏览器条上已经有刷新：这里不再摆第二颗。 */
  browsing?: boolean
}>()

const emit = defineEmits<{
  fullscreen: []
  refresh: []
}>()
</script>

<template>
  <div class="preview-head">
    <BaseButton v-if="projectId" kind="ghost" size="sm" :to="{ name: 'workspace-overview', params: { projectId } }">
      {{ t('work.room.preview.publishSite') }}
    </BaseButton>
    <v-spacer />
    <template v-if="previewUrl || previewFile">
      <BaseButton
        kind="ghost"
        icon="mdi-open-in-new"
        size="sm"
        :title="t(displayedFrame?.live ? 'work.room.preview.openLatestPreview' : 'work.room.preview.openInNewTab')"
        @click="openPreviewInNewTab(props.topicId, props.displayedFrame)"
      />
      <BaseButton
        v-if="fullscreenSupported && (previewUrl || isImageArtifact)"
        kind="ghost"
        :icon="previewFull ? 'mdi-fullscreen-exit' : 'mdi-arrow-expand-all'"
        size="sm"
        :title="previewFull ? t('work.room.preview.exitFullscreen') : t('work.room.preview.fullscreen')"
        @click="emit('fullscreen')"
      />
    </template>
    <BaseButton
      v-if="!browsing"
      kind="ghost"
      icon="mdi-refresh"
      size="sm"
      :title="t('work.room.preview.refresh')"
      :loading="refreshing"
      @click="emit('refresh')"
    />
  </div>
</template>

<style scoped>
.preview-head {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 2px;
  padding: 2px 6px;
  border-bottom: 1px solid var(--line);
}
</style>
