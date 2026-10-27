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

import { computed, defineAsyncComponent, nextTick, ref, watch } from 'vue'
import { useEventListener, useFullscreen } from '@vueuse/core'

import { usePreviewEscape } from '../../composables/usePreviewEscape'
import { t } from '../../i18n'
import { videoMimeOf } from '../../lib/fileKind'
import { MCP_APP_MIME, MCP_APP_SANDBOX } from '../../lib/mcpAppBridge'
import AttachmentVideo from '../AttachmentVideo.vue'

import { type LocatorAnchor, markPointer, measureAnchor, selectionIn } from './preview/locatorAnchor'
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
  /** 文档这一格窄下来了 / 宽回去了（`PreviewDocStage` 量的）。 */
  'doc-narrow': [narrow: boolean]
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
const isVideoArtifact = computed(() => !!props.previewFile && !!videoMimeOf(props.previewFile.path, props.previewMime))

const {
  locator,
  locatorNote,
  queued,
  annotateError,
  imageRegion,
  pageLocator,
  quoted,
  pick,
  clearLocator,
  queueLocator,
  clearQueued,
  sendQueued,
  sendLocator,
  onImageRegion,
  onPageContext,
  onQuote,
  onCell,
  onSheetRange,
  onMarkdownQuote,
  onVideoLocate,
  onAnnotate,
} = usePreviewLocator(props, {
  onLocate: (payload) => emit('locate', payload),
  onPickMode: (on) => emit('pick-mode', on),
  snapshot: (page) => docStage.value?.snapshot(page) ?? Promise.resolve(null),
  clearMark: () => docStage.value?.clearMark(),
})

// ---- 定位框弹在哪 ----
// 读者指一处（选一段、点一格、钉一点）之后，框弹在那一处旁边，而不是贴在面板底下：
// 眼睛在哪写字就在哪，指好几处时也看得出哪句话对着哪处。位置在打开那一刻记下：选中的
// 一段记成一个 Range，点出来的一处记下松手的那一点；滚动和改尺寸时重算一次。
// 记下的点只认刚刚那一下（键盘选中时没有点，框贴底），见 `preview/locatorAnchor.ts`。
const POINTER_FRESH_MS = 1500
let lastPointer: (ReturnType<typeof markPointer> & { at: number }) | null = null
let anchorRange: Range | null = null
let anchorPointer: ReturnType<typeof markPointer> | null = null
const locatorAnchor = ref<LocatorAnchor | null>(null)
useEventListener(panelElement, 'pointerup', (event: PointerEvent) => {
  const panel = panelElement.value
  if (!panel || (event.target instanceof Element && event.target.closest('.locator'))) return
  lastPointer = { ...markPointer(event, panel), at: performance.now() }
})
let placing = 0
function placeLocator() {
  // 滚动一帧可能来好几次事件：合成一帧量一次，免得每个事件都强制排一次版。
  if (placing || !locator.value) return
  placing = requestAnimationFrame(() => {
    placing = 0
    const panel = panelElement.value
    locatorAnchor.value = panel && locator.value ? measureAnchor(panel, anchorRange, anchorPointer) : null
  })
}
watch(locator, (now) => {
  if (!now) {
    anchorRange = anchorPointer = lastPointer = null
    locatorAnchor.value = null
    return
  }
  const panel = panelElement.value
  anchorRange = panel ? selectionIn(panel) : null
  anchorPointer = lastPointer && performance.now() - lastPointer.at < POINTER_FRESH_MS ? lastPointer : null
  lastPointer = null
  void nextTick(placeLocator)
})
useEventListener(panelElement, 'scroll', placeLocator, { capture: true, passive: true })
useEventListener(typeof window === 'undefined' ? null : window, 'resize', placeLocator, { passive: true })

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

/** 指认条上的「评论」出口：这句话挂成这一处的评论，不走对话。
 *
 *  指认本身不是指令——读者可能想叫队友改、也可能只想在那一处留一句话。两条出口并列，
 *  由他点哪个来定。发完把评论栏打开，让读者看见这条落在哪儿（这一栏开着才读接口）。 */
const commentError = ref('')
/** 读者动了几次指认（换一处、取消、换文件）。发评论回来时拿它比一比：中途动过就说明
 *  他已经不在这一处了，别再替他打开评论栏。 */
const pickGeneration = ref(0)
// 换了一处指认就把上一次那句话撤掉：它说的是上一处，留着会让读者以为新指的这处也失败了。
// 撤掉指认的路不止一条（条上的取消、面板上的 Escape、换一份文件、选另一段），只在取消
// 那条路上清是清不干净的——挂在指认本身变了这里，几条路一起覆盖。
watch(locator, () => {
  commentError.value = ''
  pickGeneration.value += 1
})
/** 这条出口正发着：发一条要等一会儿，这期间再点一次会挂出两条一样的评论。
 *
 *  指认条那个 `busy` 只反映页上钉一处那条路（`pageLocator.sending`），评论这条路
 *  它照不到，所以在自己这里挡一道。 */
const commenting = ref(false)
async function commentHere() {
  const body = locatorNote.value.trim()
  if (!body || !props.comments || commenting.value) return
  // 先把这一处和这句话取下来：`postFileComment` 成功后会 `clearLocator`，那时两样都没了。
  const target = commentPick.value
  const pick = target?.quote ?? null
  // 发之前记一笔：发回来时这个数变了，就说明读者中途取消或换了一处。
  // 这条出口自己收尾（不走 `postFileComment`，那个函数发完就清掉指认，会把这一笔也搅进去）。
  const before = pickGeneration.value
  commentError.value = ''
  commenting.value = true
  try {
    await props.comments.post({ content: body, quote: pick, anchor: target?.anchor ?? null })
  } catch (cause) {
    // 这条出口不经过评论栏，所以那边自己那句错误提示收不到；写在这儿，别让点了没反应。
    // 中途换过那一处就不写了：这句话说的是上一处，落在新的这一处下面会让人以为它也失败了。
    if (pickGeneration.value === before) {
      commentError.value = cause instanceof Error ? cause.message : t('work.room.comments.postFailed')
    }
    return
  } finally {
    commenting.value = false
  }
  // 发的时候读者已经按了取消（指着的那一处没了）或换成了另一处：这条评论已经挂上去了，
  // 但别再把评论栏打开——他刚刚明确表示不想留在这一处，弹出来的那一栏会盖住他正在做的事。
  if (pickGeneration.value === before) {
    clearLocator()
    if (!props.comments.open.value) props.comments.toggle()
  }
}
// 卡上写作者名字、画作者头像：都从名册那一包来（`usePanelPreview` 里那一份
// `useDocPeople`），和文档评论栏同一处。缺名册时只剩 handle 和色块——那是没接线的
// 样子，所以 `nameOf`/`avatarOf` 在这一栏是必填的 prop，不接就编译不过。
const commentNameOf = (handle: string) => props.comments?.nameOf(handle) ?? handle
const commentAvatarOf = (handle: string) => props.comments?.avatarOf(handle) ?? ''

// ---- 把已有的评论画在文件上 ----
//
// 只画没解决的那几种，和概览文档一致（那边 `openThreads` 也是只看 open 的）：一条已经
// 说完的评论还压在正文上，读者会以为它还等着人回。
//
// 而且只画**这一版**上记的那几处：评论记的是哪一版 + 哪一处，换了一版那一段话可能已经
// 挪走或改过，照旧位置画会指到别的东西上。找不回来的（正文改了）就不画——比画错强。
const focusedThread = ref<string | null>(null)
/** 这份文件上还没解决、且记在**某一版**上的那几条，各自的锚点原样交回去。
 *
 *  什么样的锚点画得出来是每一档自己的事（正文按文字找、页按页码和比例找），这里只管
 *  两条共用的判据：没解决、是这一种、记在要画的那一版上。分几处各写一遍的话，改了一处
 *  就会漂。
 *
 *  `version` 是**屏幕上那一份**的版本，不是「文件现在这一版」：页上的字节可能还是上一版
 *  的（新的一版还在路上，或者它转不出来了，旧的那份就那样留着）。照文件元数据放行的话，
 *  新一版记下的页码和比例会画在旧一版的页上，指到完全不同的地方。`null` 表示连屏幕上是
 *  哪一版都说不清——那就不画。 */
function liveAnchors(kind: string, version: string | null): { id: string; anchor: Record<string, unknown> }[] {
  if (!version) return []
  return (props.comments?.threads.state.threads ?? []).flatMap((thread) => {
    if (thread.state !== 'open') return []
    const anchor = thread.comment.anchor as Record<string, unknown> | null | undefined
    if (!anchor || anchor.kind !== kind) return []
    if (!anchor.version || anchor.version !== version) return []
    return [{ id: thread.comment.id, anchor }]
  })
}
const threadAnchors = computed(() =>
  liveAnchors('text-range', commentTarget.value?.version ?? null).flatMap(({ id, anchor }) => {
    const text = anchor.text
    if (typeof text !== 'string' || !text) return []
    return [
      {
        id,
        quote: {
          text,
          heading: (anchor.heading as string | null | undefined) ?? null,
          prefix: (anchor.prefix as string | undefined) ?? '',
          suffix: (anchor.suffix as string | undefined) ?? '',
        },
      },
    ]
  })
)
/** 分页文档上一页里指着的那几处：第几页 + 页内比例（`page-pin`）。
 *
 *  这一版按**屏幕上那份字节**算（`slideContext` 只在字节与文件版本核过、且没在加载、
 *  没出错时才给得出），和「在页上指一处」那条路用的是同一道判据。 */
const pageAnchors = computed(() =>
  liveAnchors('page-pin', props.slideContext?.version ?? null).flatMap(({ id, anchor }) => {
    const page = anchor.page
    const x = anchor.x
    const y = anchor.y
    if (typeof page !== 'number' || typeof x !== 'number' || typeof y !== 'number') return []
    return [{ id, page, x, y }]
  })
)
/** 表格上指着的那几格：工作表名 + A1 地址（`sheet-cell`）。
 *
 *  地址是文件本身给得出来的坐标，不像页码要过一次转换；所以这一档只判形状，地址在
 *  这一版里落不到任何一格上时由表格自己决定不画（表短了、那一格没画出来）。
 *  这一版照样按**屏幕上那份字节**算（`slideContext`），理由和页上那几颗同一条。 */
const sheetAnchors = computed(() => {
  const version = props.slideContext?.version ?? null
  // 两档的锚点只差在哪个字段是地址：单格是 `address`，一片的标记画在它左上角那一格
  // （锚点里两个角都在，而表格只按格画）。取地址这一步写成一处，加一档时不会漏掉形状判定。
  function pick(kind: string, field: 'address' | 'start') {
    return liveAnchors(kind, version).flatMap(({ id, anchor }) => {
      const sheet = anchor.sheet
      const address = anchor[field]
      if (typeof sheet !== 'string' || typeof address !== 'string' || !address) return []
      return [{ id, sheet, address }]
    })
  }
  return [...pick('sheet-cell', 'address'), ...pick('sheet-range', 'start')]
})
/** 图上框出来的那几处评论：原图像素的矩形，交给图片查看器按屏幕缩放画。
 *
 *  这一版同样按**屏幕上那份字节**算（`slideContext`），和页上那几颗、表里那几格同一条：
 *  一份图换过一版之后，上一版那些位置说的就不是屏幕上这张了。 */
const imageAnchors = computed(() =>
  liveAnchors('image-region', props.slideContext?.version ?? null).flatMap(({ id, anchor }) => {
    const { x, y, width, height } = anchor
    if (
      typeof x !== 'number' ||
      typeof y !== 'number' ||
      typeof width !== 'number' ||
      typeof height !== 'number' ||
      !(width > 0) ||
      !(height > 0)
    )
      return []
    return [{ id, region: { x, y, width, height } }]
  })
)
/** 点了正文上某一颗标记：把那一串评论打开（评论栏开着才去读接口，见 `comments.ts`）。 */
function openAnchor(id: string) {
  focusedThread.value = id
  if (!props.comments?.open.value) props.comments?.toggle()
}

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
      :is-image-artifact="isImageArtifact || isVideoArtifact"
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
      :versions="props.versions"
      :show-history="showHistory"
      :anchors="threadAnchors"
      :page-anchors="pageAnchors"
      :sheet-anchors="sheetAnchors"
      :live-edit="props.liveEdit"
      :active-anchor="focusedThread"
      :named-tab="!!path"
      @anchor="openAnchor"
      @close-live-edit="props.liveEdit?.end()"
      @open-editor="emit('open-editor', $event)"
      @toggle-history="emit('toggle-history')"
      @toggle-doc-page="emit('toggle-doc-page')"
      @narrow="emit('doc-narrow', $event)"
      @download="emit('download')"
      @mention-click="emit('mention-click', $event)"
      @quote="onQuote"
      @page-context="onPageContext"
      @cell="onCell"
      @sheet-range="onSheetRange"
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
      :marks="imageAnchors"
      :active-mark="focusedThread"
      :can-comment="!!commentTarget"
      :busy="commenting"
      :comment-error="commentError"
      @region="onImageRegion"
      @annotate="onAnnotate"
      @update:locator-note="locatorNote = $event"
      @send="sendLocator"
      @comment="commentHere"
      @cancel="clearLocator"
      @mark="openAnchor"
    />
    <div v-else-if="previewFile && isVideoArtifact" class="panel-preview__video">
      <AttachmentVideo
        can-locate
        :topic-id="topicId"
        :path="previewFile.path"
        @locate="onVideoLocate"
        @download="emit('download')"
      />
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
        @click="openPreviewInNewTab(topicId, displayedFrame, previewFile.path)"
      >
        {{ t('work.room.preview.openInNewWindow') }}
      </BaseButton>
    </div>
    <BaseEmptyState v-else icon="mdi-eye-off-outline" :title="t('work.room.preview.empty')" />

    <!-- 这一份文件上的评论：开着的时候盖在右边，不挤预览的画面。图上圈着一块、标注条开着时
         先收起来：矮的面板里它正落在标注条上，会盖住「取消」。 -->
    <BaseButton
      v-if="commentTarget && !imageRegion.target.value"
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
      :focus-id="focusedThread"
      :writable="true"
      :post="postFileComment"
      :name-of="commentNameOf"
      :avatar-of="commentAvatarOf"
      :agent-name="agentName"
      @clear-pick="clearLocator"
      @close="props.comments?.toggle()"
      @update:open-id="focusedThread = $event"
    />

    <PreviewLocator
      v-model:note="locatorNote"
      :target="imageRegion.target.value ? null : locator"
      :queued-count="queued.length"
      :can-queue="!pageLocator.pin.value"
      :busy="pageLocator.sending.value || commenting"
      :can-comment="!!commentTarget"
      :comment-error="commentError"
      :anchor="locatorAnchor"
      @send="sendLocator"
      @queue="queueLocator"
      @send-queued="sendQueued"
      @clear-queued="clearQueued"
      @comment="commentHere"
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
.panel-preview__video {
  display: flex;
  flex: 1 1 auto;
  align-items: center;
  justify-content: center;
  min-height: var(--preview-min);
  padding: 16px;
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
