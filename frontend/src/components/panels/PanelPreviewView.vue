<script setup lang="ts">
// 预览 tab: 芝士最后摆出来的那一样，摆在房间里让人看。
//
// **只认 props**：当前预览是哪一件、它读成的字节、文档那一页的字节、授权签下来没有
// （由 iframe 的名字决定投到哪儿），全是从外面递进来的；按刷新、按下载、处理完一处
// 修订、指着文档说一句话，都往上发事件。取数在
// `composables/usePanelPreview.ts`，`PanelPreview.vue` 那只薄容器把它接上——于是这
// 一格在测试和 /demo 里都只需要一串 props。
//
// 留在这里的是「画」和「只和这一格有关的手势」：全屏（它要的就是这个 DOM 节点）、
// 指哪里说哪句话的那个输入框。这些没有一件需要问后端。开没开编辑器、历史栏展没展开
// 不在这里记：那一层要拿它决定历史栏读的是哪一份（编辑器里那份，还是预览台上这份）。

import type { AnnotateDraft } from '../../composables/usePanelPreview'
import type { ChatAttachment } from '../../cx_types'
import type { PreviewLocate } from '../../lib/previewQuestion'
import type { RasterSelection } from './preview/designRegion'
import type { MarkdownQuote, QuoteContext } from './preview/markdownQuote'
import type { PreviewViewProps } from './preview/previewViewProps'
import type { SlidePageContext } from './preview/slidesContext'

import { computed, defineAsyncComponent, ref, watch } from 'vue'
import { useFullscreen } from '@vueuse/core'

import { usePreviewEscape } from '../../composables/usePreviewEscape'
import { t } from '../../i18n'
import { roomFileDestination } from '../../lib/previewSession'

import DesignImage from './preview/DesignImage.vue'
import DesignRegionNote from './preview/DesignRegionNote.vue'
import PreviewLocator from './preview/PreviewLocator.vue'
import PreviewMarkdown from './preview/PreviewMarkdown.vue'
import PreviewPages from './preview/PreviewPages.vue'
import PreviewPickToggle from './preview/PreviewPickToggle.vue'
import PreviewRegionPick from './preview/PreviewRegionPick.vue'
import PreviewSheet from './preview/PreviewSheet.vue'
import PreviewSlides from './preview/PreviewSlides.vue'
import RevisionList from './preview/RevisionList.vue'
import { usePreviewImageRegion } from './preview/usePreviewImageRegion'
import { usePreviewPagePin } from './preview/usePreviewPagePin'
import { usePreviewPick } from './preview/usePreviewPick'
import { usePreviewQuote } from './preview/usePreviewQuote'

import BaseButton from '@/components/base/BaseButton.vue'

// The editor and its history only load once someone opens them: most previews
// never do, and every panel that shows a preview would otherwise carry them.
const RoomFileEditor = defineAsyncComponent(() => import('./preview/RoomFileEditor.vue'))
const RoomFileHistory = defineAsyncComponent(() => import('./preview/RoomFileHistory.vue'))

const props = withDefaults(defineProps<PreviewViewProps>(), {
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
})
const emit = defineEmits<{
  (e: 'frame-load', id: number, event: Event): void
  (e: 'frame-error', id: number, event: Event): void
  /** ⋯ 里的刷新和首屏那次加载走同一条路，只是不转圈。 */
  (e: 'refresh'): void
  /** 下载当前这一份：地址和文件名都在取数那一层。 */
  (e: 'download'): void
  /** 这一份的字节变了（编辑器关了、修订处理完了、恢复了一版），按新版本重取。 */
  (e: 'document-changed'): void
  /** 读者指着文档里的一处提了一句话，交给房间的对话。 */
  (e: 'locate', payload: PreviewLocate): void
  /** 编辑器打开了一份文件：开成自由区的一个页签。 */
  (e: 'open-file', path: string): void
  /** 圈选开关变了：有桥的网页要把它递进帧（取数那一层把消息送过去）。 */
  (e: 'pick-mode', on: boolean): void
  /** 按了「编辑」：开哪一份的编辑会话由外面记。 */
  (e: 'open-editor', path: string): void
  /** 编辑器关了（或者按了叉）：外面要收掉会话并让这一页按新版本重取。 */
  (e: 'close-editor'): void
  /** 文档条上按了「历史」：那一栏的开合由外面记（编辑器那一栏也归它管）。 */
  (e: 'toggle-history'): void
  /** 点了一个人名（历史栏、编辑器上那句「谁改的」）：去他的主页由会读路由的那一层做。 */
  (e: 'mention-click', handle: string): void
}>()

const panelElement = ref<HTMLElement | null>(null)
const {
  isFullscreen: previewFull,
  isSupported: fullscreenSupported,
  toggle: toggleFullscreen,
} = useFullscreen(panelElement)
const fullscreenError = ref('')
/** 标注图传不上去时的那一句：不说的话，画完按了按钮看起来像什么都没发生。 */
const annotateError = ref('')

/** 在新标签页打开这一格看着的东西。
 *
 *  给了 `path` 就是房间里的某一份文件（自由区的一个页签）——它不跟着当前预览走，
 *  所以要把它自己的地址带过去；不给就是当前预览，那一条路本来就落在预览域根上。 */
function openPreviewInNewTab(path?: string | null) {
  if (!props.topicId) return
  // Bind the displayed file path, not latest metadata. This still serves mutable
  // room resources; it is not an immutable version or a fixed app instance.
  const displayedPath = props.displayedFrame && !props.displayedFrame.live ? props.displayedFrame.label : null
  const targetPath = displayedPath || path
  const query = targetPath ? `?path=${encodeURIComponent(roomFileDestination(targetPath))}` : ''
  window.open(`/previews/${encodeURIComponent(props.topicId)}${query}`, '_blank', 'noopener')
}

async function fullscreen() {
  fullscreenError.value = ''
  try {
    // Fullscreen keeps the same browsing context, including unsaved app state.
    await toggleFullscreen()
  } catch {
    fullscreenError.value = t('work.room.preview.fullscreenFailed')
  }
}

// 文档类交付物: a report, a deck or a budget is what the room was asked for, and
// it is shown here rather than offered as a download. A tab called 预览 that
// hands over a file instead of displaying it is the same as having no tab.
//
// Three viewers, and which one a file gets is decided by what a reader can point
// at afterwards. A paginated document keeps its text, so the reader points at a
// sentence. A spreadsheet keeps its cell addresses, and `B7` is an address 芝士
// can open directly — paginating it would destroy exactly that. A markdown file
// has neither pages nor cells, and nothing here converts it: it is shown as the
// text it already is, parsed by the same renderer the chat uses.
//
// 图片读不成文本（`content` 是 null），但那不是「没法显示」——内容域就是拿
// 对应的 image/* 把这些字节发出来的。`IMAGE_SUFFIXES` 和上面那张类型表
// 同住 `lib/fileKind`：一个文件是哪种类型只能有一个答案（取数那一层也是拿它
// 决定把不把字节交给 iframe 的）。

// 在线编辑：Word、表格、幻灯片在房间里直接改，改完存回同一份文件。编辑器开在全屏
// 对话框里；关掉之后预览按新版本重取。开没开、历史栏展没展开都记在外面（那一层同时
// 要拿它决定历史栏读哪一份），这里只按 props 画。
const EDITABLE_SUFFIXES = new Set(['docx', 'xlsx', 'pptx'])
const canEdit = computed(() => EDITABLE_SUFFIXES.has(props.documentSuffix))

const pagesRef = ref<InstanceType<typeof PreviewPages> | null>(null)

// ---- 指出位置 ----
// 读者指着文档里的一处说「这里不对」，交给芝士的是一句话：文件、位置、原文。
// 不做能长期保留的批注——读者要改的那句话，正是芝士下一轮要改掉的那句话，锚点必然
// 失效。这条评论只在下一轮被读一次，之后它属于对话记录。
const locator = ref<{ label: string; quote: string; address: string; context?: QuoteContext } | null>(null)
const locatorNote = ref('')
const imageRegion = usePreviewImageRegion(props, clearLocator)
const pageLocator = usePreviewPagePin(props, {
  snapshot: async (page) => (await pagesRef.value?.snapshot(page)) ?? null,
  open: openLocator,
  clear: clearLocator,
})
const quoted = usePreviewQuote(props, (context) => pageLocator.canUse(context))

// 网页预览里的圈选（判据和开关在 usePreviewPick）：有桥的网页由帧报回一处，应用由遮罩报回一块。
const pick = usePreviewPick({
  frame: () => props.displayedFrame,
  previewUrl: () => props.previewUrl,
  onRuntime: (on) => emit('pick-mode', on),
  open: (label, quote, address) => openLocator(label, quote, address),
  quote: quoted,
})

function openLocator(label: string, quote: string, address: string, context?: QuoteContext) {
  imageRegion.clear()
  quoted.clear()
  pageLocator.pin.value = null
  locator.value = { label, quote, address, context }
  locatorNote.value = ''
}

function clearLocator() {
  imageRegion.clear()
  locator.value = null
  quoted.clear()
  pageLocator.pin.value = null
  pagesRef.value?.clearMark()
  locatorNote.value = ''
}
// 帧里的 ESC 交给宿主：先退圈选，再收标注条，再退全屏，最后把焦点收回面板（判据在 usePreviewEscape）。
const handleEscape = usePreviewEscape(panelElement, previewFull, fullscreen, clearLocator, () => !!locator.value, pick)
defineExpose({ handleEscape, handlePick: pick.fromFrame })
function onImageRegion(selection: RasterSelection) {
  const captured = imageRegion.capture(selection)
  if (!captured) return
  openLocator(t('design.region'), t('design.selectedRegion', selection.region), '')
  imageRegion.target.value = captured
}
function onPageContext(payload: SlidePageContext) {
  if (!pageLocator.canUse(payload.context)) return
  const page = t('work.room.preview.page', { page: payload.page })
  openLocator(page, payload.scope === 'page' ? t('slides.wholePage') : payload.text.slice(0, 200), page)
  quoted.page.value = { ...payload, context: { ...payload.context } }
}

watch(
  [
    // markdown 没有下面那套文档身份，屏幕上换了一份文件时没人撤掉上一份的指认——
    // 那句话说的是文件 A 里的位置，发出去时标题上写的却是文件 B 的名字。
    () => props.previewFile?.path,
    () => props.docBytes,
    () => props.docSnapshot,
    () => props.docIdentity?.topicId,
    () => props.docIdentity?.path,
    () => props.docIdentity?.source,
    () => props.docIdentity?.taskId,
    () => props.docIdentity?.version,
    () => props.slideContext?.version,
    () => props.slideContext?.path,
    () => props.slideContext?.source,
    () => props.slideContext?.topicId,
    () => props.slideContext?.taskId,
  ],
  clearLocator,
  { flush: 'sync' }
)

function onQuote(payload: { text: string; page: number }) {
  // 一整页的选中没有指向性，当作没指。
  const quote = payload.text.replace(/\s+/g, ' ').trim()
  if (quote.length < 2) return
  const page = t('work.room.preview.page', { page: payload.page })
  openLocator(page, quote.slice(0, 200), page)
}

function onCell(payload: { address: string; value: string; sheet: string }) {
  // CSV 没有工作表名，`!B7` 会让读者以为前面漏了个名字。
  const where = payload.sheet ? `${payload.sheet}!${payload.address}` : payload.address
  openLocator(where, payload.value || t('work.room.preview.emptyCell'), where)
  quoted.cell(payload)
}

function onMarkdownQuote(payload: MarkdownQuote) {
  // 位置说的是「哪一节」而不是行号：改一句话要重新渲染，行号下一版就不成立了；它之前没有标题就是文件开头。
  const where = payload.heading
    ? t('work.room.preview.mdHeading', { heading: payload.heading })
    : t('work.room.preview.mdTop')
  openLocator(where, payload.text.slice(0, 200), where, { prefix: payload.prefix, suffix: payload.suffix })
  quoted.range(payload)
}

function contextLine({ prefix, suffix }: QuoteContext): string {
  if (prefix && suffix) return t('work.room.preview.locateContext', { prefix, suffix })
  // 空的那一侧不写——写出来只是一对空引号。
  if (prefix) return t('work.room.preview.locateContextBefore', { prefix })
  return t('work.room.preview.locateContextAfter', { suffix })
}

function sendLocator() {
  const target = locator.value
  const note = locatorNote.value.trim()
  if (!target || !note) return
  if (pageLocator.pin.value) {
    void pageLocator.send(note)
    return
  }
  if (imageRegion.target.value) {
    const message = imageRegion.message(note)
    if (!message) return
    emit('locate', { message })
    clearLocator()
    return
  }
  // 结构化引用发得出去就走它；没有可发的（null）才退回下面拼一句话那条老路。
  const sent = quoted.send(note)
  if (sent !== null) {
    if (sent) clearLocator()
    return
  }
  const message = t('work.room.preview.locateMessage', {
    path: props.previewFile?.path ?? '',
    address: target.address,
    quote: target.quote,
    note,
  })
  // 选中那段文字的两侧（markdown 才有）。两侧都是空的时候没什么可分辨的，别写进去。
  const ctx = target.context
  const context = ctx && (ctx.prefix || ctx.suffix) ? contextLine(ctx) : ''
  emit('locate', { message: context ? `${message}\n${context}` : message })
  clearLocator()
}

/** 图上画完、按了「加入对话」：把那张合成图交出去传进房间，再发那一句连同附件。
 *
 * 上传是外面递进来的能力（`uploadAnnotation`），消息要等它回来才拼得出来。发之前再
 * 核一次版本：合成的是屏幕上那张图，版本在画的过程中被人换掉时宁可不发，也不能配着
 * 一张说的不是它的图发出去。 */
async function onAnnotate(payload: AnnotateDraft) {
  const topicId = props.topicId
  const identity = props.docIdentity
  const upload = props.uploadAnnotation
  if (!topicId || !identity || !upload || !imageRegion.selectionEnabled.value) return
  let attachment: ChatAttachment
  try {
    attachment = await upload(topicId, payload)
  } catch (error) {
    annotateError.value = error instanceof Error ? error.message : String(error)
    return
  }
  if (props.topicId !== topicId) return
  annotateError.value = ''
  emit('locate', {
    message: t('design.sketchMessage', {
      ...identity,
      task: identity.taskId ?? '',
      naturalWidth: payload.naturalWidth,
      naturalHeight: payload.naturalHeight,
      count: payload.count,
      note: payload.note,
    }),
    attachments: [attachment],
  })
}
</script>

<template>
  <div ref="panelElement" class="panel-preview" tabindex="-1">
    <!-- 这一条管的都是「当前预览」：发布、新标签页打开、全屏、重读。指定了文件的那一
         格没有这些——文档和表格自己有一条带名字和下载的条，网页那一条在它自己的预览
         条上（见下面）。 -->
    <div v-if="!path" class="preview-head">
      <!-- 发布是项目级的事，落点是项目首页上那块「网站」——在房间里看着一份页面
           想把它发出去，这是唯一要跳出去的一下。 -->
      <BaseButton v-if="projectId" kind="ghost" size="sm" :to="{ name: 'workspace-running', params: { projectId } }">
        {{ t('work.room.preview.publishSite') }}
      </BaseButton>
      <v-spacer />
      <template v-if="previewUrl || previewFile">
        <BaseButton
          kind="ghost"
          icon="mdi-open-in-new"
          size="sm"
          :title="t(displayedFrame?.live ? 'work.room.preview.openLatestPreview' : 'work.room.preview.openInNewTab')"
          @click="openPreviewInNewTab()"
        />
        <!-- 图片也要全屏：它正是那种「放大才画得准」的东西，而滚轮缩放只在全屏里
             开着（见 DesignImage 的 zoomOnWheel）。 -->
        <BaseButton
          v-if="fullscreenSupported && (previewUrl || isImageArtifact)"
          kind="ghost"
          :icon="previewFull ? 'mdi-fullscreen-exit' : 'mdi-arrow-expand-all'"
          size="sm"
          :title="previewFull ? t('work.room.preview.exitFullscreen') : t('work.room.preview.fullscreen')"
          @click="fullscreen"
        />
      </template>
      <BaseButton
        kind="ghost"
        icon="mdi-refresh"
        size="sm"
        :title="t('work.room.preview.refresh')"
        :loading="refreshing"
        @click="emit('refresh')"
      />
    </div>

    <v-alert v-if="fullscreenError" type="warning" density="compact">{{ fullscreenError }}</v-alert>
    <v-alert v-if="autoReloaded" type="info" density="compact">{{ t('work.room.preview.autoReloaded') }}</v-alert>

    <div v-if="loading && !frames?.length" class="d-flex justify-center py-8">
      <v-progress-circular indeterminate color="primary" size="28" />
    </div>

    <div v-else-if="frames ? frames.length > 0 : previewUrl" class="preview-wrap">
      <div class="preview-bar text-caption px-3 pt-2">
        <span class="text-medium-emphasis">{{ displayedFrame?.label || previewAppNote || previewFile?.path }}</span>
        <span v-if="displayedFrame?.version" class="text-medium-emphasis ms-2">{{
          t('work.room.preview.readVersion', { version: displayedFrame.version })
        }}</span>
        <v-chip
          v-if="
            displayedFrame
              ? displayedFrame.live && displayedFrame.connection === 'online' && !navigationError
              : previewAppNote
          "
          size="x-small"
          variant="tonal"
          class="ms-2"
          >{{ t('work.room.preview.runningApp') }}</v-chip
        >
        <v-chip v-else size="x-small" variant="outlined" class="ms-2">{{ displayedFrame?.mime || previewMime }}</v-chip>
        <!-- 指定了文件的那一格：它不跟着当前预览走，所以顶栏那些动作（发布、新标签
             页打开）都不给它——这一份自己的两条留在这里，和文档条上那两条一样。 -->
        <template v-if="path">
          <v-spacer />
          <BaseButton
            kind="ghost"
            icon="mdi-open-in-new"
            size="sm"
            :title="t(displayedFrame?.live ? 'work.room.preview.openLatestPreview' : 'work.room.preview.openInNewTab')"
            @click="openPreviewInNewTab(path)"
          />
          <BaseButton
            kind="ghost"
            icon="mdi-download"
            size="sm"
            :title="t('work.room.preview.download')"
            @click="emit('download')"
          />
        </template>
        <PreviewPickToggle v-if="pick.target.value" :on="pick.on.value" @toggle="pick.toggle" />
      </div>
      <div v-if="displayedFrame" role="status" class="preview-runtime-status px-3 py-2 text-caption">
        <span v-if="displayedFrame.resourceId" :title="displayedFrame.resourceId">{{
          t('work.room.preview.resourceIdentity', { id: displayedFrame.resourceId.slice(0, 12) })
        }}</span>
        <span v-if="displayedFrame.instance">
          · {{ t('work.room.preview.instanceIdentity', { id: displayedFrame.instance.slice(0, 12) }) }}</span
        >
        <span v-else>
          · {{ t(displayedFrame.live ? 'work.room.preview.mutableLive' : 'work.room.preview.mutableFile') }}</span
        >
        <span>
          ·
          {{
            t(
              displayedFrame.runtime === 'ready'
                ? 'work.room.preview.runtimeReady'
                : displayedFrame.runtime === 'failed'
                  ? 'work.room.preview.runtimeFailed'
                  : 'work.room.preview.runtimeUnconfirmed'
            )
          }}</span
        >
        <span v-if="displayedFrame.connection === 'disconnected'">
          · {{ t('work.room.preview.instanceDisconnected') }}</span
        >
        <span v-if="displayedFrame.connection === 'gone'"> · {{ t('work.room.preview.instanceGone') }}</span>
        <div v-if="displayedFrame.runtimeError" role="alert">{{ displayedFrame.runtimeError }}</div>
      </div>
      <div
        v-if="navigation === 'authorizing' || navigation === 'navigating'"
        role="status"
        class="px-3 py-2 text-caption"
      >
        {{ t(navigation === 'authorizing' ? 'work.room.preview.authorizing' : 'work.room.preview.navigating') }}
      </div>
      <div v-if="navigationError || previewError || previewReadError" role="alert" class="px-3 py-2 text-error">
        {{ navigationError || previewError || previewReadError }}
        <div v-if="previewAppNote && !previewUrl">
          {{ t(previewTunnelUp ? 'tasks.preview.appUnavailable' : 'tasks.preview.connectionUnavailable') }}
        </div>
        <span v-if="displayedFrame">{{ t('work.room.preview.retainedPage') }}</span>
        <BaseButton kind="secondary" size="sm" @click="emit('refresh')">{{
          t('work.room.preview.retryTarget')
        }}</BaseButton>
      </div>
      <BaseButton v-if="path" kind="ghost" size="sm" :title="t('work.room.preview.refresh')" @click="emit('refresh')">{{
        t('work.room.preview.refresh')
      }}</BaseButton>
      <!-- Authorization still POSTs only to named sandboxed content-domain frames. -->
      <div class="preview-frames">
        <template v-if="frames">
          <iframe
            v-for="frame in frames"
            :key="frame.id"
            :name="frame.name"
            class="preview-frame"
            :class="{ 'preview-frame--incoming': frame.id !== displayedFrame?.id }"
            :inert="frame.id !== displayedFrame?.id"
            :aria-hidden="frame.id !== displayedFrame?.id"
            :tabindex="frame.id === displayedFrame?.id ? 0 : -1"
            :title="t('work.room.preview.frameTitle')"
            sandbox="allow-scripts allow-forms allow-same-origin allow-popups allow-popups-to-escape-sandbox"
            @load="emit('frame-load', frame.id, $event)"
            @error="emit('frame-error', frame.id, $event)"
          />
        </template>
        <iframe
          v-else
          :name="frameName"
          class="preview-frame"
          :title="t('work.room.preview.frameTitle')"
          sandbox="allow-scripts allow-forms allow-same-origin allow-popups allow-popups-to-escape-sandbox"
        />
        <!-- 应用那一档：没有桥，读不到页面，宿主在 iframe 上盖一层透明遮罩拖矩形。 -->
        <PreviewRegionPick v-if="pick.target.value === 'region' && pick.on.value" @pick="pick.fromRegion" />
      </div>
    </div>
    <div v-else-if="previewError || navigationError" role="alert" class="text-center text-medium-emphasis py-8">
      <v-icon size="32" class="text-error mb-2">mdi-alert-circle-outline</v-icon>
      <div>{{ t('work.room.preview.loadFailed') }}</div>
      <div class="text-caption mt-1">{{ previewError || navigationError }}</div>
      <div v-if="previewAppNote && !previewUrl" class="text-caption mt-1">
        {{ t(previewTunnelUp ? 'tasks.preview.appUnavailable' : 'tasks.preview.connectionUnavailable') }}
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
        {{ t('tasks.preview.appUnavailable') }}
      </div>
      <div v-else class="text-caption mt-1">
        {{ t('tasks.preview.connectionUnavailable') }}
      </div>
    </div>
    <div v-else-if="documentType && previewFile" class="doc">
      <div class="doc__bar">
        <v-icon size="16" class="doc__icon">{{ documentType.icon }}</v-icon>
        <span class="doc__name">{{ documentName }}</span>
        <span class="doc__type t-meta">{{ documentType.label }}</span>
        <v-spacer />
        <BaseButton
          v-if="canEdit && previewFile"
          kind="secondary"
          size="sm"
          prepend-icon="mdi-pencil-outline"
          data-testid="edit-file"
          @click="previewFile && emit('open-editor', previewFile.path)"
        >
          {{ t('work.room.preview.edit') }}
        </BaseButton>
        <BaseButton
          v-if="previewFile && !previewFile.path.startsWith('library/')"
          kind="ghost"
          size="sm"
          prepend-icon="mdi-history"
          data-testid="file-history"
          @click="emit('toggle-history')"
        >
          {{ t('work.room.preview.history') }}
        </BaseButton>
        <BaseButton kind="ghost" size="sm" prepend-icon="mdi-download" @click="emit('download')">
          {{ t('work.room.preview.download') }}
        </BaseButton>
      </div>
      <RoomFileHistory
        v-if="showHistory && topicId && previewFile"
        :file-history="props.fileHistory"
        :project-id="props.projectId"
        @mention-click="emit('mention-click', $event)"
      />

      <v-alert v-if="downloadError" type="warning" density="compact" class="mx-3 mb-2">
        {{ downloadError }}
      </v-alert>
      <!-- 刷新失败但屏幕上还留着上一版：说清楚看到的不是最新的。 -->
      <v-alert v-else-if="docError && docBytes" type="warning" density="compact" class="mx-3 mb-2">
        {{ t('work.room.preview.staleDoc', { error: docError }) }}
      </v-alert>

      <!-- Markdown 排在最前面：它不走 docBytes 那条路（loadDocument 直接跳过），
           所以下面「缺转换服务」「转不了」两句对它都不成立，先落到这里才不会
           把一篇好端端的 .md 显示成「文档预览未启用」。 -->
      <PreviewMarkdown
        v-if="documentType.view === 'markdown'"
        :source="previewFile?.content ?? null"
        @quote="onMarkdownQuote"
      />

      <!-- 只在还没有东西可看时转圈。面板每 20 秒重读一次，芝士一存文件版本就变——
           这时候把查看器卸掉重挂，读者的滚动位置和选中都没了，而新的字节本来可以
           直接换进去。 -->
      <div v-else-if="docLoading && !docBytes" class="doc__state">
        <v-progress-circular indeterminate color="primary" size="24" />
      </div>
      <!-- 两种失败说的不是一回事：一种是这个部署缺服务（换个文件也一样），一种是
           这个文件转换不了（别的文件仍然能看）。 -->
      <div v-else-if="docRendererMissing && !docBytes" class="doc__state doc__state--text">
        <v-icon size="28" class="text-disabled mb-2">mdi-eye-off-outline</v-icon>
        <div>{{ t('work.room.preview.docPreviewDisabled') }}</div>
      </div>
      <div v-else-if="docError && !docBytes" class="doc__state doc__state--text">
        <v-icon size="28" class="text-warning mb-2">mdi-file-alert-outline</v-icon>
        <div>{{ t('work.room.preview.cantDisplay') }}</div>
        <div class="t-meta mt-1">{{ docError }}</div>
      </div>
      <div v-else class="doc__body">
        <PreviewSlides
          v-if="['pptx', 'ppt', 'odp'].includes(documentSuffix)"
          :data="docBytes"
          :pending="docLoading"
          :error="docError"
          :renderer-missing="docRendererMissing"
          :context="slideContext"
          @quote="onQuote"
          @page-context="onPageContext"
        />
        <PreviewPages
          v-else-if="documentType.view === 'pages'"
          ref="pagesRef"
          :data="docBytes"
          :context="slideContext"
          @quote="onQuote"
          @pin="pageLocator.onPin"
          @dropped="clearLocator"
        />
        <PreviewSheet v-else :data="docBytes" :kind="documentType.sheet ?? 'workbook'" @cell="onCell" />

        <!-- 修订清单。页面上已经能看见改动了（LibreOffice 会把修订画出来），这里是
             用来逐条处理的。改动那一格用的是同一个组件。 -->
        <RevisionList :revs="props.revs" :path="documentSuffix === 'docx' ? previewFile.path : null" />
      </div>
    </div>
    <!-- The shown blob and region share the same original-byte snapshot. -->
    <div v-else-if="path && previewFile && isImageArtifact" class="file-image">
      <DesignImage
        v-if="imageRegion.imageSrc.value"
        :src="imageRegion.imageSrc.value"
        :alt="documentName"
        :identity="imageRegion.imageIdentity.value"
        :selection-enabled="imageRegion.selectionEnabled.value"
        :active-region="imageRegion.target.value?.selection.region ?? null"
        :active="active"
        :zoom-on-wheel="previewFull"
        @region="onImageRegion"
        @annotate="onAnnotate"
      >
        <template #region-note="{ geometry, restoreFocus, focusOrigin }">
          <DesignRegionNote
            v-if="imageRegion.target.value && locator"
            v-model:note="locatorNote"
            :target="locator"
            :geometry="geometry"
            :resource-key="imageRegion.imageIdentity.value"
            :restore-focus="restoreFocus"
            :focus-origin="focusOrigin"
            @send="sendLocator"
            @cancel="clearLocator"
          />
        </template>
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
        @click="openPreviewInNewTab(previewFile.path)"
      >
        {{ t('work.room.preview.openInNewWindow') }}
      </BaseButton>
    </div>
    <div v-else class="text-center text-medium-emphasis py-8">
      <v-icon size="32" class="text-disabled mb-2">mdi-eye-off-outline</v-icon>
      <div>{{ t('work.room.preview.empty') }}</div>
    </div>

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
     么高就停住，其余交给整块面板滚——见下面 .preview-wrap 和 .doc 的说明。 */
  --preview-min: 240px;
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  overflow-y: auto;
  background: var(--surface);
}
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
.preview-head {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 2px;
  padding: 2px 6px;
  border-bottom: 1px solid var(--line);
}
/* 填满剩下的空间，但**不许被挤没**：flex-shrink 是 0，不是 1。缩到 0 时内容不会
   跟着消失，应用条和 iframe 会溢出去盖住同一列里的别的块（定位条、编辑器）。
   shrink 归零之后高度由内容决定（应用条 + 预览自己的 240px 地板），再长就整块
   面板一起滚，谁也不盖谁。 */
.preview-wrap {
  flex: 1 0 auto;
  display: flex;
  flex-direction: column;
}
.preview-bar {
  display: flex;
  align-items: center;
}
.preview-bar > span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.preview-frame {
  flex: 1 1 auto;
  width: 100%;
  border: none;
  min-height: var(--preview-min);
  /* Theme-invariant on purpose: the iframe renders arbitrary user HTML that
     assumes a white page (its own text is near-black). Painting the backing
     dark would leave black text on a dark ground wherever that document is
     transparent — the page controls its own colours, we only back it. */
  /* stylelint-disable-next-line color-no-hex -- see the reason above */
  background: #fff;
}
.preview-runtime-status {
  overflow-wrap: anywhere;
}
.preview-frames {
  position: relative;
  flex: 1 1 auto;
  min-height: var(--preview-min);
}
.preview-frames .preview-frame {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
}
.preview-frame--incoming {
  opacity: 0;
  pointer-events: none;
}
.panel-preview:fullscreen {
  width: 100%;
  height: 100%;
}

/* ---- 文档交付物 ---- */
/* 文档这一格要能缩到面板那么高（正文自己在里面滚），但不能缩到没有——理由和上面
   .preview-wrap 是同一条：下面的列表一长，它会被挤成 0，文档条就叠在列表标题上。
   留一块地板，缩到这里就停，其余交给整块面板滚。 */
.doc {
  flex: 1 1 auto;
  min-height: var(--preview-min);
  display: flex;
  flex-direction: column;
  position: relative;
}

.doc__bar {
  flex: none;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 6px 4px 12px;
  border-bottom: 1px solid var(--line);
}
.doc__icon {
  color: var(--muted);
}
.doc__name {
  font-size: 13px;
  color: var(--ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.doc__type {
  color: var(--faint);
  flex: none;
}

.doc__state {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 32px 16px;
  color: var(--muted);
}
.doc__state--text {
  text-align: center;
}

/* 文档和修订清单并排。面板本来就窄，所以窄到一定程度就改成上下排，清单收在下面
   限高自己滚——行内修订在小屏上几乎读不了，而清单读得了。 */
.doc__body {
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  align-items: stretch;
}
.doc__body > :first-child {
  flex: 1 1 auto;
  min-width: 0;
}

.revs {
  flex: none;
  width: 236px;
  min-height: 0;
  overflow-y: auto;
  padding: 8px 12px 12px;
  border-left: 1px solid var(--line);
  background: var(--surface);
}
.revs__bar {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-bottom: 8px;
}
.revs__count {
  color: var(--muted);
}
.revs__list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.revs__item {
  padding: 8px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
}
.revs__what {
  font-size: 13px;
  color: var(--text);
  word-break: break-word;
}
.revs__who {
  margin-top: 2px;
  color: var(--faint);
}
.revs__acts {
  display: flex;
  gap: 4px;
  margin-top: 4px;
}

/* 容器查询，不是视口：这一格窄到 720 以下就把文档和修订清单摞起来。判的是
   `.panel-preview` 的宽度（本文件 714 行的 `container-type`），窗口多宽不算数。 */
@container (max-width: 720px) {
  .doc__body {
    flex-direction: column;
  }
  .revs {
    width: auto;
    max-height: 38%;
    border-left: none;
    border-top: 1px solid var(--line);
  }
}

/* .md 的正文。排版规则（标题、列表、代码块、表格）来自全局的 .md-content，
   这里只管这块地方怎么滚——面板是定高的，所以自己滚，不要让整个面板跟着长。 */
.doc__md {
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
  padding: 12px 16px 24px;
}
</style>
