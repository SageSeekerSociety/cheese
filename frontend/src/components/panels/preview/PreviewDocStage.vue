<script setup lang="ts">
// 文档类交付物那一档: a report, a deck or a budget is what the room was asked for, and
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
//
// 在线编辑：Word、表格、幻灯片在房间里直接改，改完存回同一份文件。编辑器开在全屏
// 对话框里，那一层留在壳里（`PanelPreviewView`）；这一档只管按 `editing` 之外的那几
// 样画：文档条上的「编辑」把路径发上去，开不开由外面记。
import type { PanelPreviewLiveEdit } from '@/composables/panelPreview/liveEdit'
import type { DocumentRevisionsBundle } from '@/composables/useDocumentRevisions'
import type { RoomFileHistoryBundle } from '@/composables/useRoomFileHistory'
import type { FileContent } from '@/cx_types'
import type { FileKind } from '@/lib/fileKind'
import type { MarkdownQuote } from './markdownQuote'
import type { PagePin, SlidePageContext, SlideSource } from './slidesContext'

import { computed, defineAsyncComponent, ref } from 'vue'
import { useDisplay } from 'vuetify'

import PreviewMarkdown from './PreviewMarkdown.vue'
import PreviewMarkdownLive from './PreviewMarkdownLive.vue'
import PreviewPage from './PreviewPage.vue'
import PreviewPages from './PreviewPages.vue'
import PreviewSheet from './PreviewSheet.vue'
import PreviewSlides from './PreviewSlides.vue'
import RevisionList from './RevisionList.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'
import { SLIDE_SUFFIXES } from '@/lib/fileKind'
import { previewStage } from '@/lib/previewStage'

// The history pane only loads once someone opens it: most previews never do, and
// every panel that shows a preview would otherwise carry it. Same reason, and the
// same treatment, as the editor dialog the shell keeps for itself.
const RoomFileHistory = defineAsyncComponent(() => import('./RoomFileHistory.vue'))

const props = defineProps<{
  topicId: string | null
  projectId: string | null
  documentType: FileKind | null
  documentName: string
  documentSuffix: string
  previewFile: FileContent | null
  /** 这一份有没有网页可换；没有就不摆那个开关。
   *
   *  为真只在真有第二种读法的格式上：`.doc/.odt/.rtf/.pdf` 转得成 PDF，但 OfficeCLI
   *  读不了它们，摆一个按下去没反应的按钮不如不摆。 */
  canPage?: boolean
  /** 屏幕上这一份读的是不是网页那一档。 */
  docPage?: boolean
  /** 网页那一页本身；还没取到就是 null。 */
  docPageHtml?: string | null
  docBytes: ArrayBuffer | null
  docLoading: boolean
  docError: string
  docRendererMissing: boolean
  downloadError: string
  slideContext?: SlideSource
  revs: DocumentRevisionsBundle
  fileHistory: RoomFileHistoryBundle
  showHistory: boolean
  /** 这一份文件上已有的评论指着的那几处（Markdown 里画成标记）。 */
  anchors?: readonly {
    id: string
    quote: { text: string; heading: string | null; prefix: string; suffix: string }
  }[]
  /** 分页文档（PDF、转成 PDF 的 Office）上已有的评论指着的那几处：第几页、页内比例。 */
  pageAnchors?: readonly { id: string; page: number; x: number; y: number }[]
  /** 表格上已有的评论指着的那几格：工作表名 + A1 地址（`sheet-cell` 的形状）。 */
  sheetAnchors?: readonly { id: string; sheet: string; address: string }[]
  /** 这一份 markdown 的协同编辑会话（`composables/panelPreview/liveEdit.ts` 那一包）。
   *  开着的时候正文换成编辑器。 */
  liveEdit?: PanelPreviewLiveEdit
  /** 现在打开的那一条：它那颗标记画得显眼些。 */
  activeAnchor?: string | null
}>()

const emit = defineEmits<{
  /** 按了「编辑」：开哪一份的编辑会话由外面记。 */
  'open-editor': [path: string]
  /** 按了「历史」：那一栏的开合由外面记（编辑器那一栏也归它管）。 */
  'toggle-history': []
  /** 换了一种读法（印出来的样子 / 网页）：取数那一层从此按那一种去取。 */
  'toggle-doc-page': []
  /** 下载当前这一份：地址和文件名都在取数那一层。 */
  download: []
  /** 点了一个人名（历史栏上那句「谁改的」）：去他的主页由会读路由的那一层做。 */
  'mention-click': [handle: string]
  /** 读者选了一段原文 / 指着表格里的一格 / 指着幻灯片上一处或一页。 */
  quote: [payload: { text: string; page: number }]
  'page-context': [payload: SlidePageContext]
  cell: [payload: { address: string; value: string; sheet: string }]
  /** 读者在表格里拖过一片：两个角的地址，加上这片格子里摊平的内容。 */
  'sheet-range': [payload: { sheet: string; start: string; end: string; text: string }]
  'markdown-quote': [payload: MarkdownQuote]
  /** 读者点了正文上某一颗评论标记：把那一串评论打开。 */
  anchor: [id: string]
  /** 协同编辑收起来了：这一份的编辑会话由外面关掉。 */
  'close-live-edit': []
  /** 页上钉了一处 / 那个钉不该再留了。 */
  pin: [payload: PagePin]
  dropped: []
}>()

// 在线编辑：Word、表格、幻灯片在房间里直接改，改完存回同一份文件。markdown 走另一条
// 路（预览格里和几个人一起改正文，`liveEdit`），两种都收在「编辑」这一个按钮上。
//
// markdown 那一条在手机上不开：软键盘配 Monaco 不是一件值得存下去的事，编辑器在那边
// 是只读的。让按钮露着、点开一个存不下去的只读编辑器，比不露更糟——和「改动」那一格
// 同一条（那里手机上把「编辑」换成「全文」）。Office 那几个不受影响：它们开的是全屏
// 对话框，手机上本来就是那一套。
const EDITABLE_SUFFIXES = new Set(['docx', 'xlsx', 'pptx'])
const { mdAndUp } = useDisplay()
const officeEditable = computed(() => EDITABLE_SUFFIXES.has(props.documentSuffix))
const liveEditable = computed(
  () => mdAndUp.value && props.documentType?.view === 'markdown' && !!props.liveEdit?.canEdit.value
)
const canEdit = computed(() => officeEditable.value || liveEditable.value)

function startEdit() {
  if (liveEditable.value) void props.liveEdit?.begin()
  else if (props.previewFile) emit('open-editor', props.previewFile.path)
}

// 下面那几句「转圈 / 缺服务 / 转不了」问的是「屏幕上现在有没有东西可看」，而东西
// 有两种：印刷版（PDF 的字节）和网页版（渲染好的那一页）。两种视图一次只取一种，
// 所以哪一份非空，就是哪一份在说话。
const docShown = computed(() => (props.docPage ? props.docPageHtml : props.docBytes))

// 这一刻在第几步、失败是哪一种，由 `lib/previewStage` 一处判（授权、连接、导航、就绪、
// 已更新、失败）。模板照它分岔，不再各自拼 `!docShown` 那几行条件：同一处境在任何
// 一档上说同一套名词，是这一层存在的理由。
const stage = computed(() =>
  previewStage({
    loading: props.docLoading,
    shown: !!docShown.value,
    // 上一版还在、这一版取失败了：`docError` 有值而屏幕上仍有东西。
    stale: !!props.docError && !!docShown.value,
    rendererMissing: props.docRendererMissing,
    cannotRender: !!props.docError,
  })
)

// 模板里那几处分岔的道理，写在这里，因为模板里不留中文注释（docs/i18n.md §5）：
//
//   Markdown 排在最前面 —— 它不走 `docBytes` 那条路（`loadDocument` 直接跳过），
//   所以下面「缺转换服务」「转不了」两句对它都不成立；先落到它那里，一篇好端端的
//   .md 才不会显示成「文档预览未启用」。
//   转圈 —— 只在还没有东西可看时转。面板每 20 秒重读一次，芝士一存文件版本就变，
//   这时候把查看器卸掉重挂，读者的滚动位置和选中都没了，而新的字节本来可以直接
//   换进去。
//   缺服务 / 转不了 —— 说的不是一回事：一种是这个部署缺服务（换个文件也一样），
//   一种是这个文件转换不了（别的文件仍然能看）。
//   上一版警告 —— 刷新失败但屏幕上还留着上一版：说清楚看到的不是最新的。
//   网页视图 —— 同一份文档的另一种读法：排版按屏幕来，元素带着能寻址的编号（表格的
//   /数据/B2、演示稿的 /slide[1]/shape[@id=2]），那些编号正是芝士改这份文件时要用的。
//   PDF 那条路没被换掉，默认也仍然是它。
//   修订清单 —— 页面上已经能看见改动了（LibreOffice 会把修订画出来），这里是用来逐条
//   处理的；改动那一格用的是同一个组件。

const pagesRef = ref<InstanceType<typeof PreviewPages> | null>(null)

/** 这一页此刻长什么样：钉一处时随行带上（`usePreviewLocator` 借它）。
 *
 *  这一档不摆出来（看的是别的文件、或者还没读完）时给 null。 */
function snapshot(page: number) {
  return pagesRef.value?.snapshot(page) ?? Promise.resolve(null)
}
/** 撤掉页上那个钉。 */
function clearMark() {
  pagesRef.value?.clearMark()
}
defineExpose({ snapshot, clearMark })
</script>

<template>
  <div v-if="documentType && previewFile" class="doc">
    <div class="doc__bar">
      <v-icon size="16" class="doc__icon">{{ documentType.icon }}</v-icon>
      <span class="doc__name">{{ documentName }}</span>
      <span class="doc__type t-meta">{{ documentType.label }}</span>
      <v-spacer />
      <BaseButton
        v-if="canEdit"
        kind="secondary"
        size="sm"
        prepend-icon="mdi-pencil-outline"
        data-testid="edit-file"
        @click="startEdit"
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
      <BaseButton
        v-if="canPage"
        kind="ghost"
        size="sm"
        :prepend-icon="docPage ? 'mdi-file-document-outline' : 'mdi-web'"
        :title="t(docPage ? 'work.room.preview.printViewTitle' : 'work.room.preview.webViewTitle')"
        data-testid="toggle-doc-page"
        @click="emit('toggle-doc-page')"
      >
        {{ t(docPage ? 'work.room.preview.printView' : 'work.room.preview.webView') }}
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
    <v-alert v-else-if="stage.stage === 'updated'" type="warning" density="compact" class="mx-3 mb-2">
      {{ t('work.room.preview.staleDoc', { error: docError }) }}
    </v-alert>
    <!-- 按了「编辑」，但这一份开不了协同（太大、二进制、部署没接）：说一句为什么，
         别让那一下看着像什么都没发生。只在这一份还停在阅读视图时摆。 -->
    <v-alert
      v-else-if="liveEdit?.refused.value && !liveEdit.editingPath.value"
      type="warning"
      density="compact"
      class="mx-3 mb-2"
      data-testid="live-refused"
    >
      {{ liveEdit.refused.value }}
    </v-alert>

    <PreviewMarkdownLive
      v-if="documentType.view === 'markdown' && liveEdit?.editingPath.value"
      :live="liveEdit"
      @close="emit('close-live-edit')"
    />
    <PreviewMarkdown
      v-else-if="documentType.view === 'markdown'"
      :source="previewFile?.content ?? null"
      :anchors="anchors"
      :active-id="activeAnchor"
      @quote="emit('markdown-quote', $event)"
      @anchor="emit('anchor', $event)"
    />

    <div v-else-if="stage.stage === 'navigate'" class="doc__state">
      <v-progress-circular indeterminate color="primary" size="24" />
    </div>
    <div
      v-else-if="stage.stage === 'failed' && stage.failure === 'rendererMissing'"
      class="doc__state doc__state--text"
    >
      <v-icon size="28" class="text-disabled mb-2">mdi-eye-off-outline</v-icon>
      <div>{{ t('work.room.preview.docPreviewDisabled') }}</div>
    </div>
    <div v-else-if="stage.stage === 'failed' && stage.failure === 'cannotRender'" class="doc__state doc__state--text">
      <v-icon size="28" class="text-warning mb-2">mdi-file-alert-outline</v-icon>
      <div>{{ t('work.room.preview.cantDisplay') }}</div>
      <div class="t-meta mt-1">{{ docError }}</div>
    </div>
    <div v-else-if="docPage" class="doc__body">
      <PreviewPage :html="docPageHtml ?? null" />
    </div>
    <div v-else class="doc__body">
      <PreviewSlides
        v-if="SLIDE_SUFFIXES.has(documentSuffix)"
        :data="docBytes"
        :pending="docLoading"
        :error="docError"
        :renderer-missing="docRendererMissing"
        :context="slideContext"
        @quote="emit('quote', $event)"
        @page-context="emit('page-context', $event)"
      />
      <PreviewPages
        v-else-if="documentType.view === 'pages'"
        ref="pagesRef"
        :data="docBytes"
        :context="slideContext"
        :marks="pageAnchors"
        :active-mark="activeAnchor"
        @quote="emit('quote', $event)"
        @page-context="emit('page-context', $event)"
        @pin="emit('pin', $event)"
        @mark="emit('anchor', $event)"
        @dropped="emit('dropped')"
      />
      <PreviewSheet
        v-else
        :data="docBytes"
        :kind="documentType.sheet ?? 'workbook'"
        :marks="sheetAnchors"
        :active-mark="activeAnchor"
        @cell="emit('cell', $event)"
        @range="emit('sheet-range', $event)"
        @mark="emit('anchor', $event)"
      />

      <RevisionList :revs="props.revs" :path="documentSuffix === 'docx' ? previewFile.path : null" />
    </div>
  </div>
</template>

<style scoped src="./stageState.css"></style>

<style scoped>
/* 文档这一格要能缩到面板那么高（正文自己在里面滚），但不能缩到没有——理由和
   预览条底下那一段是同一条：下面的列表一长，它会被挤成 0，文档条就叠在列表标题上。
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

@media (max-width: 720px) {
  .doc__body {
    flex-direction: column;
  }
}

/* .md 的正文。排版规则（标题、列表、代码块、表格）来自全局的 .md-content，
   这里只管这块地方怎么滚——面板是定高的，所以自己滚，不要让整个面板跟着长。
   这一条落在 `MarkdownView` 的根元素上（`PreviewMarkdown` 的根就是它），所以
   它写在这一档里而不是那个组件里。 */
.doc__md {
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
  padding: 12px 16px 24px;
}
</style>
