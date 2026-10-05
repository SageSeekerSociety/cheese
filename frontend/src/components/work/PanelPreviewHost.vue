<script setup lang="ts">
// 「预览」那一格的接线外壳。
//
// 面板里那一半（`components/panels/PanelPreview.vue`）只吃 props：场景棘轮认的「场景」是
// `components/panels/**` 下每个 SFC，A 档的意思是「给一组 props 就能单独出画面」，取数
// 一滴都不能漏进去。所以这一格所有的取数——当前预览是哪一件、授权、20 秒轮询、文档字节、
// 在线编辑器的会话、那一栏历史、这一份 .docx 的修订——都在
// `composables/usePanelPreview.ts` 里调一次，结果原样递下去。
//
// 外壳只能待在这儿：`components/panels/**` 底下每个 SFC 都是场景，包括外壳自己，所以它
// 得站在场景之外；`components/**` 又不许直接 import 接口层（`pnpm run lint:boundary`），
// 所以取数走 `composables/`。同一条理由见 `components/work/PanelDocHost.vue`。
import type { PreviewLocate, SubmitPreviewQuestion } from '../../lib/previewQuestion'

import { ref, useId } from 'vue'

import { usePanelPreview } from '../../composables/usePanelPreview'
import PanelPreview from '../panels/PanelPreview.vue'

const props = withDefaults(
  defineProps<{
    topicId: string | null
    submitQuestion?: SubmitPreviewQuestion
    projectId: string | null
    // This tab is the one on screen. Loads happen on the rising edge.
    active?: boolean
    // Bumped by WorkPanel when a turn ends — silent re-fetch, never a spinner.
    refreshTick?: number
    // 自由区的一个页签：这一格只看房间里这一份文件，不跟着当前预览走。
    path?: string | null
  }>(),
  { active: false, refreshTick: 0, path: null, submitQuestion: undefined }
)
const emit = defineEmits<{
  (e: 'loaded', artifactId: string | null): void
  (e: 'locate', payload: PreviewLocate): void
  (e: 'open-file', path: string): void
  (e: 'mention-click', handle: string): void
}>()

// 授权表的落点：取数那一层拿着这个名字把表单投出去，展示组件把它写在 iframe 上。
// 两边必须是同一个名字——名字没对上，浏览器会开一个新标签页。
const frameName = `cheese-preview-${useId()}`

// 帧里的 ESC 和圈选要在展示组件里处理（标注条和全屏都是它那一格的状态），所以取数那一层
// 拿到的是这一只，两边靠上面那两个 defineExpose 的入口接上。
const panelRef = ref<InstanceType<typeof PanelPreview> | null>(null)

const preview = usePanelPreview(props, {
  frameName,
  // 元数据回来一次就报一次：房间拿它标「预览有新内容」。
  onLoaded: (artifactId) => emit('loaded', artifactId),
  onEscape: () => panelRef.value?.handleEscape(),
  onPick: (pick) => panelRef.value?.handlePick(pick),
  // 「另存一份」另存出了新的一份：开成自由区的一个页签。
  onOpenFile: (path) => emit('open-file', path),
})
</script>

<template>
  <PanelPreview
    ref="panelRef"
    :topic-id="props.topicId"
    :submit-question="props.submitQuestion"
    :project-id="props.projectId"
    :active="props.active"
    :refresh-tick="props.refreshTick"
    :path="props.path"
    :frame-name="frameName"
    :preview="preview"
    @loaded="emit('loaded', $event)"
    @locate="emit('locate', $event)"
    @open-file="emit('open-file', $event)"
    @mention-click="emit('mention-click', $event)"
  />
</template>
