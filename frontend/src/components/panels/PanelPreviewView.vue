<script setup lang="ts">
// 预览 tab: 芝士最后摆出来的那一样，摆在房间里让人看。
//
// **只认 props**：当前预览是哪一件、它读成的字节、文档那一页的字节、授权签下来没有
// （由 iframe 的名字决定投到哪儿），全是从外面递进来的；按刷新、按下载、处理完一处
// 修订、指着文档说一句话，都往上发事件。取数在
// `composables/usePanelPreview.ts`，`PanelPreview.vue` 那只薄容器把它接上——于是这
// 一格在测试和 /demo 里都只需要一串 props。
//
// 这一只现在是一层壳：**底下那几支「没东西可看」的兜底、定位条、编辑器对话框**，加上
// 挑哪一支摆出来的先后。每一支自己的画法和手势在 `preview/` 下的组件里：
//
//   PreviewHeadBar      顶栏（发布、新标签页、全屏、重读）
//   PreviewFrameStage   一个跑着的东西（应用、网页）那一档，含运行时状态和 frames
//   PreviewDocStage     文档、表格、幻灯片、markdown 那一档，含修订清单和历史栏
//   PreviewImageStage   图片那一档，含图上标注
//   PreviewLocator      底下的定位条
//
// 「指哪里说哪句话」那一整套（四指针、标注上传、换文件时撤掉上一处指认）在
// `preview/usePreviewLocator.ts`：它是跨档的——图上那一块、页上一处、表里一格都从
// 那里收口成同一条消息。
//
// 留在这里的还有全屏：它要的就是这个 DOM 节点，所以 `panelElement` 在这一只身上。

import type { PreviewLocate } from '../../lib/previewQuestion'
import type { QuotedContext } from '../../lib/quotedContext'
import type { PreviewViewProps } from './preview/previewViewProps'

import { computed, defineAsyncComponent, ref } from 'vue'
import { useFullscreen } from '@vueuse/core'

import { usePreviewEscape } from '../../composables/usePreviewEscape'
import { t } from '../../i18n'
import { MCP_APP_MIME, MCP_APP_SANDBOX } from '../../lib/mcpAppBridge'

import PreviewComments from './preview/PreviewComments.vue'
import PreviewDocStage from './preview/PreviewDocStage.vue'
import PreviewFrameStage from './preview/PreviewFrameStage.vue'
import PreviewHeadBar from './preview/PreviewHeadBar.vue'
import PreviewImageStage from './preview/PreviewImageStage.vue'
import PreviewLocator from './preview/PreviewLocator.vue'
import { openPreviewInNewTab } from './preview/previewNewTab'
import { usePreviewLocator } from './preview/usePreviewLocator'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'

// The editor and its history only load once someone opens them: most previews
// never do, and every panel that shows a preview would otherwise carry them.
const RoomFileEditor = defineAsyncComponent(() => import('./preview/RoomFileEditor.vue'))

const props = withDefaults(defineProps<PreviewViewProps>(), {
  agentName: () => t('work.room.defaultAgentName'),
  submitQuestion: undefined,
  uploadAnnotation: undefined,
  active: true,
  path: null,
  autoReloaded: false,
  frames: undefined,
  displayedFrame: null,
  navigation: 'idle',
  navigationError: '',
  docIdentity: null,
  docSnapshot: null,
  slideContext: undefined,
  docPage: false,
  canPage: false,
  docPageHtml: null,
})
const emit = defineEmits<{
  'frame-load': [id: number, event: Event]
  'frame-error': [id: number, event: Event]
  /** ⋯ 里的刷新和首屏那次加载走同一条路，只是不转圈。 */
  refresh: []
  /** 下载当前这一份：地址和文件名都在取数那一层。 */
  download: []
  /** 这一份的字节变了（编辑器关了、修订处理完了、恢复了一版），按新版本重取。 */
  'document-changed': []
  /** 读者指着文档里的一处提了一句话，交给房间的对话。 */
  locate: [payload: PreviewLocate]
  /** 编辑器打开了一份文件：开成自由区的一个页签。 */
  'open-file': [path: string]
  /** 圈选开关变了：有桥的网页要把它递进帧（取数那一层把消息送过去）。 */
  'pick-mode': [on: boolean]
  /** 按了「编辑」：开哪一份的编辑会话由外面记。 */
  'open-editor': [path: string]
  /** 编辑器关了（或者按了叉）：外面要收掉会话并让这一页按新版本重取。 */
  'close-editor': []
  /** 文档条上按了「历史」：那一栏的开合由外面记（编辑器那一栏也归它管）。 */
  'toggle-history': []
  /** 文档条上换了一种读法（印出来的样子 / 网页）：取数那一层从此按那一种去取。 */
  'toggle-doc-page': []
  /** 点了一个人名（历史栏、编辑器上那句「谁改的」）：去他的主页由会读路由的那一层做。 */
  'mention-click': [handle: string]
}>()

// 房间自己的 artifact 与应用共用一套沙箱：它们从内容域发出来，所以带着
// `allow-same-origin`。第三方 MCP View 不能带 —— `allow-scripts` 加
// `allow-same-origin` 会把手写这份 HTML 的人放进内容域的存储里。同一个面板、
// 同一个预览源，沙箱按这一帧的 mime 分：房内那套不变，第三方那套收紧（见
// `lib/mcpAppBridge.ts`，宿主 CSP 也做了同样的收口）。
const DEFAULT_SANDBOX = 'allow-scripts allow-forms allow-same-origin allow-popups allow-popups-to-escape-sandbox'
function sandboxFor(mime?: string | null): string {
  return mime === MCP_APP_MIME ? MCP_APP_SANDBOX : DEFAULT_SANDBOX
}

const panelElement = ref<HTMLElement | null>(null)
const {
  isFullscreen: previewFull,
  isSupported: fullscreenSupported,
  toggle: toggleFullscreen,
} = useFullscreen(panelElement)
const fullscreenError = ref('')

async function fullscreen() {
  fullscreenError.value = ''
  try {
    // Fullscreen keeps the same browsing context, including unsaved app state.
    await toggleFullscreen()
  } catch {
    fullscreenError.value = t('work.room.preview.fullscreenFailed')
  }
}

// 文档那一档的实例：钉在页上的一处要它当时那张图，换文件时又要它把那个钉撤掉。
const docStage = ref<InstanceType<typeof PreviewDocStage> | null>(null)

const {
  locator,
  locatorNote,
  annotateError,
  imageRegion,
  pageLocator,
  quoted,
  pick,
  clearLocator,
  sendLocator,
  onImageRegion,
  onPageContext,
  onQuote,
  onCell,
  onMarkdownQuote,
  onAnnotate,
} = usePreviewLocator(props, {
  onLocate: (payload) => emit('locate', payload),
  onPickMode: (on) => emit('pick-mode', on),
  snapshot: (page) => docStage.value?.snapshot(page) ?? Promise.resolve(null),
  clearMark: () => docStage.value?.clearMark(),
})

// ---- 这一份文件上的评论 ----
// 开着没有是这一格自己的事（和全屏、标注条一样）；线程和写入从 `comments` 递进来。
const showComments = computed(() => props.comments?.open.value ?? false)
const commentTarget = computed(() => props.comments?.target.value ?? null)
// 读者此刻指着的那一处：页上一点、表里一格、正文一段、网页里一处。没指就是整份文件。
const commentPick = computed(() => {
  const anchor = pageLocator.anchor() ?? quoted.anchor()
  const target = locator.value
  if (!anchor && !target) return null
  // 位置和原文两样都说：钉在页上时位置是「第 3 页」，原文是「从左 42%、从上 60%」，
  // 只留一样就说不清指的是哪一页的哪一处。
  const quote = [target?.label, target?.quote].filter((part) => part && part.trim()).join(' · ')
  return { quote, anchor }
})
/** 发一条评论；成不成由这一栏自己说，发出去之后把指认撤掉。 */
async function postFileComment(payload: { content: string; quote: string | null; anchor: QuotedContext | null }) {
  await props.comments?.post(payload)
  clearLocator()
}
// 卡上写作者名字、画作者头像：都从名册那一包来（`usePanelPreview` 里那一份
// `useDocPeople`），和文档评论栏同一处。缺名册时只剩 handle 和色块——那是没接线的
// 样子，所以 `nameOf`/`avatarOf` 在这一栏是必填的 prop，不接就编译不过。
const commentNameOf = (handle: string) => props.comments?.nameOf(handle) ?? handle
const commentAvatarOf = (handle: string) => props.comments?.avatarOf(handle) ?? ''

// 帧里的 ESC 交给宿主：先退圈选，再收标注条，再退全屏，最后把焦点收回面板（判据在 usePreviewEscape）。
const handleEscape = usePreviewEscape(panelElement, previewFull, fullscreen, clearLocator, () => !!locator.value, pick)
defineExpose({ handleEscape, handlePick: pick.fromFrame })
</script>

<template>
  <div ref="panelElement" class="panel-preview" tabindex="-1">
    <!-- 这一条管的都是「当前预览」：发布、新标签页打开、全屏、重读。指定了文件的那一
         格没有这些——文档和表格自己有一条带名字和下载的条，网页那一条在它自己的预览
         条上（见 PreviewFrameStage）。 -->
    <PreviewHeadBar
      v-if="!path"
      :topic-id="topicId"
      :project-id="projectId"
      :preview-url="previewUrl"
      :preview-file="previewFile"
      :displayed-frame="displayedFrame"
      :fullscreen-supported="fullscreenSupported"
      :is-image-artifact="isImageArtifact"
      :preview-full="previewFull"
      :refreshing="refreshing"
      @fullscreen="fullscreen"
      @refresh="emit('refresh')"
    />

    <v-alert v-if="fullscreenError" type="warning" density="compact">{{ fullscreenError }}</v-alert>
    <v-alert v-if="autoReloaded" type="info" density="compact">{{ t('work.room.preview.autoReloaded') }}</v-alert>

    <div v-if="loading && !frames?.length" class="d-flex justify-center py-8">
      <v-progress-circular indeterminate color="primary" size="28" />
    </div>
    <PreviewFrameStage
      v-else-if="frames ? frames.length > 0 : previewUrl"
      :topic-id="topicId"
      :path="path"
      :frame-name="frameName"
      :frames="frames"
      :displayed-frame="displayedFrame"
      :navigation="navigation"
      :navigation-error="navigationError"
      :preview-file="previewFile"
      :preview-mime="previewMime"
      :preview-url="previewUrl"
      :preview-app-note="previewAppNote"
      :preview-tunnel-up="previewTunnelUp"
      :preview-error="previewError"
      :preview-read-error="previewReadError"
      :pick-target="pick.target.value"
      :pick-on="pick.on.value"
      :agent-name="agentName"
      @refresh="emit('refresh')"
      @frame-load="(id: number, event: Event) => emit('frame-load', id, event)"
      @frame-error="(id: number, event: Event) => emit('frame-error', id, event)"
      @toggle-pick="pick.toggle"
      @pick-region="pick.fromRegion"
      @download="emit('download')"
    />
    <div v-else-if="previewError || navigationError" role="alert" class="text-center text-medium-emphasis py-8">
      <v-icon size="32" class="text-error mb-2">mdi-alert-circle-outline</v-icon>
      <div>{{ t('work.room.preview.loadFailed') }}</div>
      <div class="text-caption mt-1">{{ previewError || navigationError }}</div>
      <div v-if="previewAppNote && !previewUrl" class="text-caption mt-1">
        {{
          t(previewTunnelUp ? 'tasks.preview.appUnavailable' : 'tasks.preview.connectionUnavailable', {
            agent: agentName,
          })
        }}
      </div>
      <BaseButton kind="secondary" size="sm" @click="emit('refresh')">{{
        t('work.room.preview.retryTarget')
      }}</BaseButton>
    </div>
    <div v-else-if="previewReadError" class="text-center text-medium-emphasis py-8">
      <v-icon size="32" class="text-warning mb-2">mdi-file-alert-outline</v-icon>
      <div>{{ t('work.room.preview.readFailed') }}</div>
      <div v-if="path" class="text-caption mt-1">
        {{ t('work.room.preview.pathError', { path, error: previewReadError }) }}
      </div>
      <div v-else class="text-caption mt-1">
        {{
          previewNamedPath
            ? t('work.room.preview.pathError', { path: previewNamedPath, error: previewReadError })
            : previewReadError
        }}
      </div>
    </div>
    <div v-else-if="previewNamed && previewAppNote" class="text-center text-medium-emphasis py-8">
      <!-- Two states, and they are not interchangeable: the machine is not
           carrying a preview out at all, or it is and the app behind it is
           gone. Collapsing them told people to summon 芝士 again for a tunnel
           that no summon brings back. -->
      <v-icon size="32" class="text-disabled mb-2">mdi-lan-disconnect</v-icon>
      <div>{{ t('tasks.preview.unavailable') }}</div>
      <div v-if="previewTunnelUp" class="text-caption mt-1">
        {{ t('tasks.preview.appUnavailable', { agent: agentName }) }}
      </div>
      <div v-else class="text-caption mt-1">
        {{ t('tasks.preview.connectionUnavailable', { agent: agentName }) }}
      </div>
    </div>
    <PreviewDocStage
      v-else-if="documentType && previewFile"
      ref="docStage"
      :topic-id="topicId"
      :project-id="projectId"
      :document-type="documentType"
      :document-name="documentName"
      :document-suffix="documentSuffix"
      :preview-file="previewFile"
      :can-page="canPage"
      :doc-page="docPage"
      :doc-page-html="docPageHtml"
      :doc-bytes="docBytes"
      :doc-loading="docLoading"
      :doc-error="docError"
      :doc-renderer-missing="docRendererMissing"
      :download-error="downloadError"
      :slide-context="slideContext"
      :revs="revs"
      :file-history="fileHistory"
      :show-history="showHistory"
      @open-editor="emit('open-editor', $event)"
      @toggle-history="emit('toggle-history')"
      @toggle-doc-page="emit('toggle-doc-page')"
      @download="emit('download')"
      @mention-click="emit('mention-click', $event)"
      @quote="onQuote"
      @page-context="onPageContext"
      @cell="onCell"
      @markdown-quote="onMarkdownQuote"
      @pin="pageLocator.onPin"
      @dropped="clearLocator"
    />
    <!-- The shown blob and region share the same original-byte snapshot. -->
    <PreviewImageStage
      v-else-if="path && previewFile && isImageArtifact"
      :document-name="documentName"
      :active="active"
      :preview-full="previewFull"
      :image-region="imageRegion"
      :locator-note="locatorNote"
      :locator="locator"
      :doc-error="docError"
      :annotate-error="annotateError"
      @region="onImageRegion"
      @annotate="onAnnotate"
      @update:locator-note="locatorNote = $event"
      @send="sendLocator"
      @cancel="clearLocator"
    />
    <div v-else-if="previewFile && previewFile.content === null" class="text-center text-medium-emphasis py-8">
      <v-icon size="32" class="text-warning mb-2">mdi-file-alert-outline</v-icon>
      <div>{{ t('work.room.preview.notText') }}</div>
      <div class="text-caption mt-1">{{ t('work.room.preview.notTextDetail', { path: previewFile.path }) }}</div>
      <!-- 指定了文件的那一格也走这条路：内容域按路径服务房间里的任意一份，所以那
           一句话在这一格同样成立——它带着文件自己的地址过去。 -->
      <BaseButton
        kind="secondary"
        size="sm"
        class="mt-3"
        prepend-icon="mdi-open-in-new"
        @click="openPreviewInNewTab(topicId, displayedFrame, previewFile.path)"
      >
        {{ t('work.room.preview.openInNewWindow') }}
      </BaseButton>
    </div>
    <BaseEmptyState v-else icon="mdi-eye-off-outline" :title="t('work.room.preview.empty')" />

    <!-- 这一份文件上的评论：开着的时候盖在右边，不挤预览的画面。 -->
    <BaseButton
      v-if="commentTarget"
      class="panel-preview__comments-toggle"
      kind="secondary"
      size="sm"
      prepend-icon="mdi-comment-text-outline"
      :title="t('work.room.comments.title')"
      @click="props.comments?.toggle()"
    >
      {{ t('work.room.comments.title') }}
    </BaseButton>
    <PreviewComments
      v-if="showComments && commentTarget"
      class="panel-preview__comments"
      :topic-id="commentTarget.topicId"
      :path="commentTarget.path"
      :version="commentTarget.version"
      :author="props.comments?.author ?? ''"
      :thread-state="props.comments!.threads.state"
      :thread-actions="props.comments!.threads.actions"
      :pick="commentPick"
      :writable="true"
      :post="postFileComment"
      :name-of="commentNameOf"
      :avatar-of="commentAvatarOf"
      :agent-name="agentName"
      @clear-pick="clearLocator"
      @close="props.comments?.toggle()"
    />

    <PreviewLocator
      v-model:note="locatorNote"
      :target="imageRegion.target.value ? null : locator"
      :busy="pageLocator.sending.value"
      @send="sendLocator"
      @cancel="clearLocator"
    />

    <v-dialog
      :model-value="!!editing"
      fullscreen
      @update:model-value="(open: boolean) => !open && emit('close-editor')"
    >
      <RoomFileEditor
        v-if="editing && topicId"
        :key="editing"
        :path="editing"
        :project-id="props.projectId"
        :editor="props.editor"
        :file-history="props.fileHistory"
        @close="emit('close-editor')"
        @mention-click="emit('mention-click', $event)"
      />
    </v-dialog>
  </div>
</template>

<style scoped>
.panel-preview {
  position: relative;
  container-type: inline-size;
  /* 上面那一格的地板：一面预览低到看不出东西就不叫预览了。列高不够时它缩到这
     么高就停住，其余交给整块面板滚——见 `PreviewFrameStage` 和 `PreviewDocStage`
     里各自那条说明。 */
  --preview-min: 240px;
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  overflow-y: auto;
  background: var(--surface);
}
.panel-preview:fullscreen {
  width: 100%;
  height: 100%;
}

/* 评论栏盖在预览的右边：这一格是看东西的，评论是旁边的话，不该把画面挤窄。 */
.panel-preview__comments {
  position: absolute;
  inset: 0 0 0 auto;
  z-index: var(--z-raised);
  width: min(340px, 100%);
  box-shadow: var(--shadow-2);
}
.panel-preview__comments-toggle {
  position: absolute;
  right: 12px;
  bottom: 64px;
  z-index: var(--z-raised);
}
</style>
