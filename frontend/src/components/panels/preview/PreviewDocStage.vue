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
// text it already is, parsed by the same renderer the chat uses. Source code
// keeps its line numbers, and `第 12–18 行` is the address a reader points at.
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
import type { PreviewVersionsBundle } from '@/composables/panelPreview/versions'
import type { DocumentRevisionsBundle } from '@/composables/useDocumentRevisions'
import type { RoomFileHistoryBundle } from '@/composables/useRoomFileHistory'
import type { FileContent } from '@/cx_types'
import type { FileKind } from '@/lib/fileKind'
import type { MarkdownQuote } from './markdownQuote'
import type { PagePin, SlidePageContext, SlideSource } from './slidesContext'

import { computed, defineAsyncComponent, inject, nextTick, ref, watch } from 'vue'
import { useDisplay } from 'vuetify'

import { PREVIEW_MODE } from '@/composables/panelPreview/previewMode'

import PreviewCode from './PreviewCode.vue'
import PreviewMarkdown from './PreviewMarkdown.vue'
import PreviewMarkdownLive from './PreviewMarkdownLive.vue'
import PreviewModeSwitch from './PreviewModeSwitch.vue'
import PreviewPage from './PreviewPage.vue'
import PreviewPages from './PreviewPages.vue'
import PreviewSheet from './PreviewSheet.vue'
import PreviewSlides from './PreviewSlides.vue'
import PreviewVersionDiff from './PreviewVersionDiff.vue'
import PreviewVersionPicker from './PreviewVersionPicker.vue'
import PreviewVersionStrip from './PreviewVersionStrip.vue'
import RevisionList from './RevisionList.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'
import { SLIDE_SUFFIXES } from '@/lib/fileKind'
import { usePreviewNarrow } from '@/lib/previewNarrow'
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
  /** 文件名旁「第 N 版」、下面那条「本版共修改 N 处」、对比态和看旧版（`composables/panelPreview/versions.ts`）。
   *  不给就不摆。 */
  versions?: PreviewVersionsBundle
  showHistory: boolean
  /** 这一份文件上已有的评论指着的那几处（Markdown 里画成标记）。 */
  anchors?: readonly {
    id: string
    quote: { text: string; heading: string | null; prefix: string; suffix: string }
  }[]
  /** 攒在待发篮里、还没发出去的那几处（Markdown 里在那一句话末尾挂一颗序号）。
   *  `n` 是它在篮里的编号，和 tray 上那一行对得上。 */
  pins?: readonly {
    id: string
    n: number
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
  /** 这一格挂在写着文件名的页签上：文档条上不再重复一遍文件名，只留图标和类型。 */
  namedTab?: boolean
}>()

const emit = defineEmits<{
  /** 按了「编辑」：开哪一份的编辑会话由外面记。 */
  'open-editor': [path: string]
  /** 按了「历史」：那一栏的开合由外面记（编辑器那一栏也归它管）。 */
  'toggle-history': []
  /** 换了一种读法（印出来的样子 / 网页）：取数那一层从此按那一种去取。 */
  'toggle-doc-page': []
  /** 这一格窄下来了 / 宽回去了：窄的时候 Word 默认读网页那一份（取数那一层据此挑取哪一份）。 */
  narrow: [narrow: boolean]
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
  /** 读者在代码里指了几行（`PreviewCode`）。 */
  'code-lines': [payload: { start: number; end: number; text: string }]
  /** 读者点了正文上某一颗评论标记：把那一串评论打开。 */
  anchor: [id: string]
  /** 协同编辑收起来了：这一份的编辑会话由外面关掉。 */
  'close-live-edit': []
  /** 页上钉了一处 / 那个钉不该再留了。 */
  pin: [payload: PagePin]
  dropped: []
}>()

// 在线编辑：Word、表格、幻灯片在房间里直接改，改完存回同一份文件。markdown 走另一条
// 路（预览格里和几个人一起改正文，`liveEdit`）。
//
// 入口只有文档条上「改」那一段（原型里也是那一段，没有另摆一颗「编辑」键）：
// 能不改由 `PREVIEW_MODE` 那一份判，它同时管着那一段灰不灰。
//
// **手机上两种都不开**（负责人 2026-10-28 定）：手机上没有值得用的编辑体验。markdown
// 那条是软键盘配 Monaco，编辑器在那边本来就是只读；Office 那条更糟——OnlyOffice 把
// 桌面版那一套塞进 390px 的宽度里，还会卡在「正在载入」里。让入口露着、点开一个改不动
// 或根本打不开的编辑器，比不露更糟：读者花时间点进去，得到的是一次白等。和「改动」那一
// 格同一条（那里手机上把「编辑」换成「全文」）。
const { mdAndUp } = useDisplay()
const liveEditable = computed(
  () => mdAndUp.value && props.documentType?.view === 'markdown' && !!props.liveEdit?.canEdit.value
)

function startEdit() {
  if (liveEditable.value) void props.liveEdit?.begin()
  else if (props.previewFile) emit('open-editor', props.previewFile.path)
}

// 切到「改」就开这一份的编辑会话——markdown 走协同、Office 走全屏对话框；离开「改」
// 就把协同那一栏收掉，免得停在「看」而正文还在改。
const modes = inject(PREVIEW_MODE, null)
const editingNow = computed(() => modes?.editing.value ?? false)
watch(editingNow, (editing) => {
  if (editing) startEdit()
  else if (liveEditable.value) props.liveEdit?.end()
})

// 下面那几句「转圈 / 缺服务 / 转不了」问的是「屏幕上现在有没有东西可看」，而东西
// 有两种：印刷版（PDF 的字节）和网页版（渲染好的那一页）。两种视图一次只取一种，
// 所以哪一份非空，就是哪一份在说话。
const docShown = computed(() => (props.docPage ? props.docPageHtml : props.docBytes))

// 屏幕上有没有可读的东西、在取的那一份走到哪一步，由 `lib/previewStage` 一处判。模板照
// 它分岔，不再各自拼 `!docShown` 那几行条件：同一处境在任何一档上说同一套名词，是这一层
// 存在的理由。
//
// 这一档只报「在取」这一件（`navigate`），不报「在等授权」：它取的是文件字节，没有帧那
// 一档那条要先拿会话的授权。`loading` 因此只可能是 `navigate` 或 null。
const stage = computed(() =>
  previewStage({
    loading: props.docLoading ? 'navigate' : null,
    shown: !!docShown.value,
    // 上一版还在、这一版取失败了：`docError` 有值而屏幕上仍有东西。
    stale: !!props.docError && !!docShown.value,
    rendererMissing: props.docRendererMissing,
    cannotRender: !!props.docError,
  })
)

// 模板里那几处分岔的道理，写在这里，因为模板里不留中文注释（docs/i18n.md §5）：
//
//   Markdown 和代码排在最前面 —— 它们不走 `docBytes` 那条路（`loadDocument` 直接
//   跳过），所以下面「缺转换服务」「转不了」两句对它们都不成立；先落到它们那里，一篇
//   好端端的 .md 才不会显示成「文档预览未启用」。代码读不成文本（二进制、太大）时就地
//   说一句，不往下落：下面最后那一支是表格。
//   转圈 —— 只在还没有东西可看时转。面板每 20 秒重读一次，芝士一存文件版本就变，
//   这时候把查看器卸掉重挂，读者的滚动位置和选中都没了，而新的字节本来可以直接
//   换进去。
//   缺服务 / 转不了 —— 说的不是一回事：一种是这个部署缺服务（换个文件也一样），
//   一种是这个文件转换不了（别的文件仍然能看）。
//   上一版警告 —— 刷新失败但屏幕上还留着上一版：说清楚看到的不是最新的。
//   网页视图 —— 同一份文档的另一种读法：排版按屏幕来，元素带着能寻址的编号（表格的
//   /数据/B2、演示稿的 /slide[1]/shape[@id=2]），那些编号正是芝士改这份文件时要用的。
//   宽处默认仍是 PDF；窄的一格里 Word 默认读这一份，并且按屏宽重排（`reflow`），不再是一页
//   缩小的纸。
//   修订清单 —— 页面上已经能看见改动了（LibreOffice 会把修订画出来），这里是用来逐条
//   处理的；改动那一格用的是同一个组件。

// 「窄」按这一格自己的宽度算（右栏、手机上的一格），不看窗口：窄到放不下这几颗按钮
// 就把它们收进「…」，文档条始终一行。还没量到宽度时按宽处画，量到了再收。
const docRef = ref<HTMLElement | null>(null)
const { narrow: compact, measured } = usePreviewNarrow(docRef)
watch(
  [compact, measured],
  ([narrow, known]) => {
    if (known) emit('narrow', narrow)
  },
  { immediate: true }
)
/** 窄处的 Word 网页版按屏宽重排：页宽、页边距、分页都拿掉，字号照原文。 */
const reflow = computed(() => compact.value && props.documentSuffix === 'docx')

const pagesRef = ref<InstanceType<typeof PreviewPages> | null>(null)
const showSource = ref(false)
// 代码那一档要的正文：读不成文本（二进制、太大）时是 null，就地说明一句，不往下落。
const codeSource = computed(() => (props.documentType?.view === 'code' ? props.previewFile?.content ?? null : null))
// 代码长行默认横向滚：代码的缩进和对齐是意思的一部分，折行会把它打乱。窄面板上读者
// 可能宁可折行也不想左右拖，所以留一颗开关。
const codeWrap = ref(false)
watch(
  () => props.previewFile?.path,
  () => {
    showSource.value = false
  }
)

// ---- 版本 ----
// 版本行、提示条、对比态、旧版：取数和判断都在 `versions` 那一包里，这里只决定摆在哪。
// 协同编辑开着时不摆：编辑器里看到的是正在改的正文，不是哪一版。
const version = computed(() => (props.liveEdit?.editingPath.value ? null : props.versions?.current.value ?? null))
const compare = computed(() => (version.value ? props.versions?.compare.value ?? null : null))
const oldView = computed(() => (version.value ? props.versions?.oldView.value ?? null : null))
/** 对比态里逐段标出增删的那一种（Markdown、Word）；表格的改动画在格子上，不换正文。 */
const textCompare = computed(() => {
  const changes = compare.value?.changes
  return changes?.kind === 'text' ? changes : null
})
const pickMode = computed(() =>
  props.versions?.canView.value ? 'view' : props.versions?.canCompare.value ? 'compare' : 'none'
)
const sheetChanged = computed(() => (oldView.value ? null : props.versions?.changedCells.value ?? null))

const diffRef = ref<InstanceType<typeof PreviewVersionDiff> | null>(null)
const changeIndex = ref(0)
function goChange(index: number) {
  changeIndex.value = index
  diffRef.value?.focusChange(index)
}
// 比出来了就停在第一处：读者点「查看改动」是要看改了哪，不是从文档开头往下找。
watch(textCompare, (changes) => {
  changeIndex.value = 0
  if (changes?.count) void nextTick(() => diffRef.value?.focusChange(0))
})

/** 文档条上「编辑」之外的那几件：宽时平铺成 BaseButton，窄时收进「…」里的一行。
 *  「编辑」不在这里——它是这一格里让事情往下走的那一颗，留在条上不折叠。 */
type DocAction = {
  key: string
  label: string
  icon: string
  title?: string
  /** 平铺那颗按钮的 data-testid（窄时的菜单行不挂它）。 */
  testid?: string
  disabled?: boolean
  /** 按下去的那种（「对比」开着时）。 */
  active?: boolean
  run: () => void
}
const actions = computed<DocAction[]>(() => {
  const list: DocAction[] = []
  // 有版本行时「历史」收进它的菜单（菜单底下那一条），条上不再重复摆一颗。
  if (props.previewFile && !props.previewFile.path.startsWith('library/') && !version.value) {
    list.push({
      key: 'history',
      icon: 'mdi-history',
      label: t('work.room.preview.history'),
      testid: 'file-history',
      run: () => emit('toggle-history'),
    })
  }
  if (props.versions?.canCompare.value && !props.liveEdit?.editingPath.value) {
    const on = !!props.versions.compare.value
    list.push({
      key: 'compare',
      icon: 'mdi-compare-horizontal',
      label: t('work.room.versions.compare'),
      title: t('work.room.versions.compareTitle'),
      testid: 'version-compare',
      active: on,
      run: () => (on ? props.versions?.exitCompare() : props.versions?.openCompare()),
    })
  }
  if (props.canPage) {
    list.push({
      key: 'doc-page',
      icon: props.docPage ? 'mdi-file-document-outline' : 'mdi-web',
      label: t(props.docPage ? 'work.room.preview.printView' : 'work.room.preview.webView'),
      title: t(props.docPage ? 'work.room.preview.printViewTitle' : 'work.room.preview.webViewTitle'),
      testid: 'toggle-doc-page',
      run: () => emit('toggle-doc-page'),
    })
  }
  if (
    props.documentType?.view === 'markdown' &&
    !props.liveEdit?.editingPath.value &&
    props.previewFile?.content !== null
  ) {
    list.push({
      key: 'md-source',
      icon: showSource.value ? 'mdi-eye-outline' : 'mdi-code-tags',
      label: t(showSource.value ? 'work.room.preview.webView' : 'work.room.preview.showSource'),
      testid: 'toggle-md-source',
      run: () => {
        showSource.value = !showSource.value
      },
    })
  }
  if (props.documentType?.view === 'code' && props.previewFile?.content !== null) {
    list.push({
      key: 'code-wrap',
      icon: codeWrap.value ? 'mdi-arrow-expand-horizontal' : 'mdi-wrap',
      label: t(codeWrap.value ? 'work.room.preview.codeNoWrap' : 'work.room.preview.codeWrap'),
      testid: 'toggle-code-wrap',
      run: () => {
        codeWrap.value = !codeWrap.value
      },
    })
  }
  list.push({
    key: 'download',
    icon: 'mdi-download',
    label: t('work.room.preview.download'),
    run: () => emit('download'),
  })
  return list
})

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
  <div v-if="documentType && previewFile" ref="docRef" class="doc">
    <div class="doc__bar">
      <v-icon size="16" class="doc__icon">{{ documentType.icon }}</v-icon>
      <span v-if="!namedTab" class="doc__name">{{ documentName }}</span>
      <span class="doc__type t-meta">{{ documentType.label }}</span>
      <!-- 看 / 批注 / 改 三段（原型里那一段，快捷键 1/2/3）。摆在这一条右边，和版本
           行同一行——原型里也是这一行。窄面板收成图标。 -->
      <PreviewModeSwitch class="doc__modes" :compact="compact" />
      <PreviewVersionPicker
        v-if="version && versions"
        :list="versions.list.value"
        :current="version"
        :shown="oldView?.seq ?? version.seq"
        :compact="compact"
        :on-pick="pickMode"
        @choose="versions.choose($event)"
        @history="emit('toggle-history')"
      />
      <v-spacer />
      <v-menu v-if="compact" location="bottom end">
        <template #activator="{ props: menuProps }">
          <BaseButton
            v-bind="menuProps"
            kind="ghost"
            icon="mdi-dots-horizontal"
            size="sm"
            density="comfortable"
            data-testid="doc-more"
            :title="t('work.room.menu.more')"
            :aria-label="t('work.room.menu.more')"
          />
        </template>
        <v-list density="compact" role="menu">
          <v-list-item
            v-for="action in actions"
            :key="action.key"
            role="menuitem"
            :prepend-icon="action.icon"
            :disabled="action.disabled"
            :active="action.active"
            @click="action.run()"
          >
            <v-list-item-title>{{ action.label }}</v-list-item-title>
          </v-list-item>
        </v-list>
      </v-menu>
      <template v-else>
        <BaseButton
          v-for="action in actions"
          :key="action.key"
          kind="ghost"
          size="sm"
          :prepend-icon="action.icon"
          :title="action.title"
          :disabled="action.disabled"
          :active="action.active"
          :data-testid="action.testid"
          @click="action.run()"
        >
          {{ action.label }}
        </BaseButton>
      </template>
    </div>
    <RoomFileHistory
      v-if="showHistory && topicId && previewFile"
      :file-history="props.fileHistory"
      :project-id="props.projectId"
      @mention-click="emit('mention-click', $event)"
    />
    <PreviewVersionStrip
      v-if="version && versions"
      :hint="versions.hint.value"
      :compare="compare"
      :old-seq="oldView?.seq ?? null"
      :old-error="versions.oldError.value"
      :can-compare="versions.canCompare.value"
      :change-index="changeIndex"
      :compact="compact"
      @view-changes="versions.openCompare()"
      @dismiss="versions.dismissHint()"
      @exit-compare="versions.exitCompare()"
      @compare-current="versions.openCompare(oldView?.seq)"
      @back="versions.backToLatest()"
      @prev="goChange(changeIndex - 1)"
      @next="goChange(changeIndex + 1)"
    />

    <v-alert v-if="downloadError" type="warning" density="compact" class="mx-3 mb-2">
      {{ downloadError }}
    </v-alert>
    <v-alert v-else-if="stage.stage === 'shown' && stage.stale" type="warning" density="compact" class="mx-3 mb-2">
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

    <PreviewVersionDiff
      v-if="textCompare"
      ref="diffRef"
      :rows="textCompare.diff.rows"
      :markdown="textCompare.markdown"
      :compact="compact"
    />
    <template v-else-if="oldView">
      <PreviewMarkdown v-if="oldView.kind === 'markdown' && oldView.text !== null" :source="oldView.text" />
      <div v-else-if="oldView.kind !== 'markdown' && oldView.bytes" class="doc__body">
        <PreviewSheet v-if="oldView.kind === 'sheet'" :data="oldView.bytes" :kind="documentType.sheet ?? 'workbook'" />
        <PreviewPages v-else :data="oldView.bytes" />
      </div>
      <div v-else-if="!versions?.oldError.value" class="doc__state">
        <v-progress-circular indeterminate color="primary" size="24" />
      </div>
    </template>
    <PreviewMarkdownLive
      v-else-if="documentType.view === 'markdown' && liveEdit?.editingPath.value"
      :live="liveEdit"
      @close="emit('close-live-edit')"
    />
    <PreviewCode
      v-else-if="documentType.view === 'markdown' && showSource"
      data-testid="markdown-source"
      :source="previewFile?.content ?? ''"
      language="markdown"
      wrap
    />
    <PreviewMarkdown
      v-else-if="documentType.view === 'markdown'"
      :source="previewFile?.content ?? null"
      :anchors="anchors"
      :pins="pins"
      :active-id="activeAnchor"
      @quote="emit('markdown-quote', $event)"
      @anchor="emit('anchor', $event)"
    />
    <template v-else-if="documentType.view === 'code'">
      <PreviewCode
        v-if="codeSource !== null"
        :source="codeSource"
        :language="documentType.language ?? null"
        :wrap="codeWrap"
        @lines="emit('code-lines', $event)"
      />
      <div v-else class="doc__state doc__state--text">
        <v-icon size="28" class="text-warning mb-2">mdi-file-alert-outline</v-icon>
        <div>{{ t('work.room.preview.notText') }}</div>
      </div>
    </template>

    <div v-else-if="stage.stage === 'waiting' && stage.step === 'navigate'" class="doc__state">
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
      <PreviewPage :html="docPageHtml ?? null" :reflow="reflow" />
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
        :changed="sheetChanged"
        :changed-by="versions?.changedBy.value"
        :show-before="!!compare"
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
  flex-wrap: nowrap;
  align-items: center;
  gap: 8px;
  padding: 4px 6px 4px 12px;
  border-bottom: 1px solid var(--line);
}
.doc__icon {
  color: var(--muted);
}
/* 只有文件名让位：条上其余几件（图标、类型标签、按钮）都不该被挤变形。 */
.doc__name {
  flex: 0 1 auto;
  min-width: 0;
  font-size: 13px;
  color: var(--ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.doc__type {
  color: var(--faint);
  flex: none;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
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
