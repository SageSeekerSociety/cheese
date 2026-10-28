// 「预览」这一格里那份文档的字节：浏览器画不出来的先转 PDF，其余读原始字节。
// 「改动」那一格取的是同一份东西（`lib/documentBytes`），所以这件事只写在一处。
//
// 读者能挑另一种读法：网页（`docPage`）。两种读法各自取各自的那一份，同一时刻只有一种在
// 取——PDF 那一边的 `enabled` 因此要看这个开关。挑法不跟着文件走：一份文档读成什么样是阅读
// 习惯，不是这份文件的属性，换一份不该把它翻回去。
//
// 窄的一格里（手机、右栏被挤窄）Word 默认读网页那一份，再由 `PreviewPage` 按屏宽重排：分页
// 那一份在 390 宽上正文只有六七个像素，读不了。读者自己按过开关就照他按的来，不再自动切。
// 只有 Word：表格和演示稿各有自己为窄屏做的读法，网页那一份对它们并不更好读。
//
// 网页那一档里「整页引用」够不着：它要的是刚画出来的那一页和文件版本对得上（下面的
// `slideContext`），而那一页在一个沙箱帧里，选中的字回不到这边。读者要指着整页说话就切回
// 分页视图——那里的话仍然作数。
import type { DocumentIdentity } from '../../lib/documentIdentity'
import type { PreviewState } from './previewState'
import type { PanelPreviewProps } from './types'

import { computed, ref } from 'vue'

import { useDocumentBytes, useDocumentPage } from '../../lib/documentBytes'
import { sameDocumentIdentity } from '../../lib/documentIdentity'
import { pageViewOf, suffixOf } from '../../lib/fileKind'
import { NARROW_PX } from '../../lib/previewNarrow'

export interface DocumentBytesDeps {
  props: PanelPreviewProps
  /** 这一格摆着的那一份：文档身份、是不是图，都从它算。 */
  state: PreviewState
}

/** 这一格那份文档的字节，和读者挑的分页/网页两种读法。 */
export function usePanelPreviewDocument(deps: DocumentBytesDeps) {
  const { props, state } = deps

  const docNonce = ref(0)
  /** 读者按过的那一下；null 是没按过，由宽窄决定。 */
  const docPage = ref<boolean | null>(null)
  /** 这一格窄不窄，由画它的那一层量了报上来（`setNarrow`）。量到之前按窗口猜一次，手机上
   *  第一次取的就是网页那一份，不先白取一份 PDF。 */
  const narrow = ref(typeof window !== 'undefined' && window.innerWidth < NARROW_PX)
  const docIdentity = computed<DocumentIdentity | null>(() => {
    const file = state.previewFile.value
    if (!props.topicId || !file) return null
    return {
      topicId: props.topicId,
      path: file.path,
      taskId: null,
      source: file.source ?? 'live',
      version: file.version,
    }
  })
  const canPage = computed(() => pageViewOf(docIdentity.value?.path))
  const reflowable = computed(() => suffixOf(docIdentity.value?.path ?? '') === 'docx')
  const wantPage = computed(() => canPage.value && (docPage.value ?? (narrow.value && reflowable.value)))
  const docSource = {
    topicId: () => docIdentity.value?.topicId ?? null,
    path: () => docIdentity.value?.path ?? null,
    version: () => docIdentity.value?.version ?? null,
    task: () => docIdentity.value?.taskId ?? null,
    source: () => docIdentity.value?.source ?? 'live',
    nonce: () => docNonce.value,
  }
  const {
    bytes: docBytes,
    snapshot: docSnapshot,
    loading: bytesLoading,
    error: bytesError,
    rendererMissing: bytesRendererMissing,
  } = useDocumentBytes({
    ...docSource,
    // markdown 和代码读的就是 `previewFile.content` 那段文字，不取字节。
    enabled: () =>
      !wantPage.value &&
      ((!!state.documentType.value && !['markdown', 'code'].includes(state.documentType.value.view)) ||
        (!!props.path && state.isImageArtifact.value)),
  })
  const {
    html: docPageHtml,
    loading: pageLoading,
    error: pageError,
    rendererMissing: pageRendererMissing,
  } = useDocumentPage({ ...docSource, enabled: () => wantPage.value })
  // 面板只认一套状态：屏幕上同时只可能是一种读法，所以递上去的是正在显示的那一种的状态，
  // 模板里那几个「转圈」「缺服务」「转不了」的分支不用按读法分两遍写。
  const docLoading = computed(() => (wantPage.value ? pageLoading.value : bytesLoading.value))
  const docError = computed(() => (wantPage.value ? pageError.value : bytesError.value))
  const docRendererMissing = computed(() => (wantPage.value ? pageRendererMissing.value : bytesRendererMissing.value))

  function toggleDocPage() {
    docPage.value = !wantPage.value
  }
  function setNarrow(value: boolean) {
    narrow.value = value
  }
  const slideContext = computed(() => {
    const current = docIdentity.value
    const displayed = docSnapshot.value
    if (
      !current?.version ||
      !displayed ||
      docLoading.value ||
      docError.value ||
      docRendererMissing.value ||
      !sameDocumentIdentity(current, displayed.identity) ||
      displayed.sourceVersion !== current.version
    )
      return undefined
    return { ...current, version: current.version }
  })

  /**
   * 处理完一处修订、关掉在线编辑器、恢复了一版草稿：文件都变了，而这一页的字节是按文件版本
   * 缓存的，版本却没变（是这里改的，不是芝士改的），所以自己打一下让它重取。
   */
  function refreshDocument() {
    docNonce.value += 1
  }

  return {
    docBytes,
    docIdentity,
    docSnapshot,
    slideContext,
    docLoading,
    docError,
    docRendererMissing,
    // 递下去的是「这一份现在读成网页」，不是读者手上那个开关：开关是粘的（换文件不翻回去），
    // 而屏幕上画得出来的只有这一份能画的那一种。两者只在能换的格式上相等——那正是开关露面的
    // 地方——所以按钮的文案和图标不受影响。
    docPage: wantPage,
    canPage,
    docPageHtml,
    toggleDocPage,
    setNarrow,
    refreshDocument,
  }
}
