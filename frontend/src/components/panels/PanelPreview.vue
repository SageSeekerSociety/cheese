<script setup lang="ts">
// 话题页右侧「预览」那一格 —— 一只薄容器。
//
// 它以前是一个 994 行的组件：自己引七个接口函数、自己按 20 秒轮询、自己决定
// 什么时候把授权表投进 iframe，然后又自己把那一切画出来。于是「预览」这个界面在测试
// 和 /demo 里都必须先立一个假后端（`views/demo/demoPanels.ts` 里那两条路由就是为它
// 写的）。
//
// 现在两边分家，和 #2130 拆 PanelChanges 是同一个形状：
//   - 取数（当前预览是哪一件、字节读成什么、授权、轮询、文档字节、在线编辑、历史）
//     → `composables/usePanelPreview.ts`，由 `components/work/PanelPreviewHost.vue`
//     调一次，整包递进来
//   - 画（用哪种查看器、空态写哪句话、全屏按钮在不在）
//     → `components/panels/PanelPreviewView.vue`，只凭 props 渲染
// 这一只只负责把两边接起来：状态摊开递下去、事件接回来。加取数动作在组合式函数里加，
// 加画法在展示组件里加，这一只基本不再长。
//
// 它是场景（`components/panels/**` 下每个 SFC 都是），所以取数一滴都不能漏进来。
import type { PanelPreviewBundle } from '../../composables/usePanelPreview'
import type { FramePick } from '../../composables/usePreviewFrames'
import type { PreviewLocate, SubmitPreviewQuestion } from '../../lib/previewQuestion'

import { ref } from 'vue'

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
    // 授权表的落点：取数那一层拿着这个名字把表单投出去，展示组件把它写在 iframe 上。
    // 两边必须是同一个名字——名字没对上，浏览器会开一个新标签页。
    frameName: string
    /** 这一格的取数（`composables/usePanelPreview.ts` 那一包）。 */
    preview: PanelPreviewBundle
  }>(),
  { active: false, refreshTick: 0, path: null, submitQuestion: undefined }
)
const emit = defineEmits<{
  (e: 'loaded', artifactId: string | null): void
  /** 读者指着文档里的一处提了一句话，交给房间的对话；图上画过东西时随行带那张图。 */
  (e: 'locate', payload: PreviewLocate): void
  /** 编辑器打开了一份文件：开成自由区的一个页签。 */
  (e: 'open-file', path: string): void
  /** 点了一个人名（历史栏、编辑器上那句「谁改的」）：去他的主页由会读路由的那一层做。 */
  (e: 'mention-click', handle: string): void
}>()

// 摊开而不是留着那一包：这一格的接口就是这二十来样东西，谁传谁看得见。摊开之后模板里
// 那些名字还是老样子（`frames`、`previewFile`……），因为它们现在都是顶层的 ref。
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
  docPage,
  canPage,
  docPageHtml,
  revs,
  editing,
  showHistory,
  editor,
  fileHistory,
  // 动作
  load,
  downloadArtifact,
  refreshDocument,
  uploadAnnotation,
  openEditor,
  closeEditor,
  toggleHistory,
  toggleDocPage,
  setPickMode,
} = props.preview

// 帧里的 ESC 要在展示组件里处理（标注条和全屏都是它那一格的状态），所以这里留一个
// 引用：取数那一层收到 escape 就调它的 handleEscape。
const previewView = ref<InstanceType<typeof PanelPreviewView> | null>(null)

// 取数那一层拿到的是这一只，中间多一层不该让那两条线断掉。
defineExpose({
  handleEscape: () => previewView.value?.handleEscape(),
  handlePick: (pick: FramePick) => previewView.value?.handlePick(pick),
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
    :frame-name="props.frameName"
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
    :doc-page="docPage"
    :can-page="canPage"
    :doc-page-html="docPageHtml"
    :revs="revs"
    :editing="editing"
    :show-history="showHistory"
    :editor="editor"
    :file-history="fileHistory"
    @frame-load="frameLoaded"
    @frame-error="frameFailed"
    @pick-mode="setPickMode"
    @refresh="refresh"
    @download="downloadArtifact"
    @document-changed="refreshDocument"
    @locate="emit('locate', $event)"
    @open-file="emit('open-file', $event)"
    @open-editor="openEditor"
    @close-editor="closeEditor"
    @toggle-history="toggleHistory"
    @toggle-doc-page="toggleDocPage"
    @mention-click="emit('mention-click', $event)"
  />
</template>
