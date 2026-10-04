<script setup lang="ts">
// 话题页右侧「预览」那一格 —— 一只薄容器。
//
// 它以前是一个 994 行的组件：自己 import 七个接口函数、自己按 20 秒轮询、自己决定
// 什么时候把授权表投进 iframe，然后又自己把那一切画出来。于是「预览」这个界面在测试
// 和 /demo 里都必须先立一个假后端（`views/demo/demoPanels.ts` 里那两条路由就是为它
// 写的）。
//
// 现在两边分家，和 #2130 拆 PanelChanges 是同一个形状：
//   - 取数（当前预览是哪一件、字节读成什么、授权、轮询、文档字节）
//     → `composables/usePanelPreview.ts`
//   - 画（用哪种查看器、空态写哪句话、全屏按钮在不在）
//     → `components/panels/PanelPreviewView.vue`，只凭 props 渲染
// 这一只只负责把两边接起来：状态递下去、事件接回来。加取数动作在组合式函数里加，
// 加画法在展示组件里加，这一只基本不再长。
import type { PreviewLocate, SubmitPreviewQuestion } from '../../lib/previewQuestion'

import { ref, useId } from 'vue'

import { usePanelPreview } from '../../composables/usePanelPreview'

import PanelPreviewView from './PanelPreviewView.vue'

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
  /** 读者指着文档里的一处提了一句话，交给房间的对话；图上画过东西时随行带那张图。 */
  (e: 'locate', payload: PreviewLocate): void
  /** 编辑器打开了一份文件：开成自由区的一个页签。 */
  (e: 'open-file', path: string): void
}>()

// 授权表的落点：取数那一层拿着这个名字把表单投出去，展示组件把它写在 iframe 上。
// 两边必须是同一个名字——名字没对上，浏览器会开一个新标签页。
const frameName = `cheese-preview-${useId()}`

// 帧里的 ESC 要在展示组件里处理（标注条和全屏都是它那一格的状态），所以这里留一个
// 引用：取数那一层收到 escape 就调它的 handleEscape。
const previewView = ref<InstanceType<typeof PanelPreviewView> | null>(null)

const {
  frames,
  displayedFrame,
  navigation,
  navigationError,
  frameLoaded,
  frameFailed,
  loading,
  refreshing,
  previewFile,
  previewMime,
  previewNamed,
  previewUrl,
  previewAppNote,
  previewTunnelUp,
  previewNamedPath,
  autoReloaded,
  previewError,
  previewReadError,
  documentSuffix,
  documentType,
  documentName,
  isImageArtifact,
  downloadError,
  docBytes,
  docIdentity,
  docSnapshot,
  slideContext,
  docLoading,
  docError,
  docRendererMissing,
  // 动作
  load,
  downloadArtifact,
  refreshDocument,
  uploadAnnotation,
  setPickMode,
} = usePanelPreview(props, {
  frameName,
  // 元数据回来一次就报一次：房间拿它标「预览有新内容」。
  onLoaded: (artifactId) => emit('loaded', artifactId),
  onEscape: () => previewView.value?.handleEscape(),
  // 帧里圈选了一处：标注条和引用都在展示组件里，取数这一层只把这一处递下去。
  onPick: (pick) => previewView.value?.handlePick(pick),
})

// ⋯ 里的刷新和首屏那次加载走同一条路，只是不转圈：按了刷新就是要重取，不再比对
// 「还是不是同一件」。
function refresh() {
  void load({ silent: true, reload: true })
}
</script>

<template>
  <!-- 一次 props 面摊开，而不是 v-bind 一整包：这二十来样东西就是这一格的接口，
       谁传谁看得见；将来哪一样不传了，typecheck 也会点名。 -->
  <PanelPreviewView
    ref="previewView"
    :topic-id="props.topicId"
    :submit-question="props.submitQuestion"
    :upload-annotation="uploadAnnotation"
    :active="props.active"
    :project-id="props.projectId"
    :path="props.path"
    :frame-name="frameName"
    :frames="frames"
    :displayed-frame="displayedFrame"
    :navigation="navigation"
    :navigation-error="navigationError"
    :loading="loading"
    :refreshing="refreshing"
    :preview-file="previewFile"
    :preview-mime="previewMime"
    :preview-named="previewNamed"
    :preview-url="previewUrl"
    :preview-app-note="previewAppNote"
    :preview-tunnel-up="previewTunnelUp"
    :preview-named-path="previewNamedPath"
    :auto-reloaded="autoReloaded"
    :preview-error="previewError"
    :preview-read-error="previewReadError"
    :document-suffix="documentSuffix"
    :document-type="documentType"
    :document-name="documentName"
    :is-image-artifact="isImageArtifact"
    :download-error="downloadError"
    :doc-bytes="docBytes"
    :doc-identity="docIdentity"
    :doc-snapshot="docSnapshot"
    :slide-context="slideContext"
    :doc-loading="docLoading"
    :doc-error="docError"
    :doc-renderer-missing="docRendererMissing"
    @frame-load="frameLoaded"
    @frame-error="frameFailed"
    @pick-mode="setPickMode"
    @refresh="refresh"
    @download="downloadArtifact"
    @document-changed="refreshDocument"
    @locate="emit('locate', $event)"
    @open-file="emit('open-file', $event)"
  />
</template>
