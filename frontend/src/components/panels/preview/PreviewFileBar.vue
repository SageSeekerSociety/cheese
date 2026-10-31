<script setup lang="ts">
// 网页、SVG、图片顶上那一条：类型、「看 / 批注 / 改」三段、第几版。
//
// 文档类（PDF、Word、表格、Markdown、代码）的这一条在 `PreviewDocStage` 里，带着它们自己
// 的大纲、对比、看原文。这三类没有那些，但「现在是在读还是在指」「这是第几版、谁改的」
// 是同一件事，读者换一种文件不该换一套说法：三段和版本行照文档条摆。「改」在这三类上
// 开不起来，键是灰的，悬停说为什么（`PreviewModeSwitch` 自己判）。
//
// 旧版在这几类上画不出来，版本菜单里点旧版是灰的，底下一条去历史里下载（`onPick: 'none'`）。
import type { PreviewVersionsBundle } from '@/composables/panelPreview/versions'
import type { RoomFileHistoryBundle } from '@/composables/useRoomFileHistory'

import { computed, defineAsyncComponent, ref } from 'vue'

import PreviewModeSwitch from './PreviewModeSwitch.vue'
import PreviewVersionPicker from './PreviewVersionPicker.vue'

import { usePreviewNarrow } from '@/lib/previewNarrow'

const RoomFileHistory = defineAsyncComponent(() => import('./RoomFileHistory.vue'))

const props = defineProps<{
  icon: string
  label: string
  versions?: PreviewVersionsBundle
  showHistory: boolean
  fileHistory?: RoomFileHistoryBundle
  projectId?: string | null
  /** 有话题页才有历史栏可开（资料库、产物页那几处没有）。 */
  topicId?: string | null
}>()

const emit = defineEmits<{
  'toggle-history': []
  'mention-click': [handle: string]
}>()

const root = ref<HTMLElement | null>(null)
const { narrow } = usePreviewNarrow(root)
const version = computed(() => props.versions?.current.value ?? null)
</script>

<template>
  <div ref="root" class="file-bar-wrap">
    <div class="file-bar" data-testid="preview-file-bar">
      <v-icon size="16" class="file-bar__icon">{{ icon }}</v-icon>
      <span class="file-bar__type t-meta">{{ label }}</span>
      <PreviewModeSwitch :compact="narrow" />
      <PreviewVersionPicker
        v-if="version && versions"
        :list="versions.list.value"
        :current="version"
        :shown="version.seq"
        :compact="narrow"
        on-pick="none"
        @history="emit('toggle-history')"
      />
      <v-spacer />
      <slot />
    </div>
    <RoomFileHistory
      v-if="showHistory && topicId && fileHistory"
      :file-history="fileHistory"
      :project-id="projectId ?? null"
      @mention-click="emit('mention-click', $event)"
    />
  </div>
</template>

<style scoped>
/* 和文档条同一号：高、边线、内边距照 `.doc__bar`，换一种文件时这一行不跳。 */
.file-bar {
  display: flex;
  flex: none;
  flex-wrap: nowrap;
  align-items: center;
  gap: 8px;
  min-width: 0;
  padding: 4px 6px 4px 12px;
  border-bottom: 1px solid var(--line);
}
.file-bar__icon {
  color: var(--muted);
}
.file-bar__type {
  flex: none;
  color: var(--faint);
  white-space: nowrap;
}
</style>
