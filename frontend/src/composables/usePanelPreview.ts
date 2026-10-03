// 「预览」那一格的取数：它调的那些接口、它的 20 秒轮询、它按版本重取的文档字节。
//
// 和画的那一半（`components/panels/PanelPreviewView.vue`）分家的理由，和「改动」那次
// 一样：这一格原先自己 import 七个接口函数、自己按 20 秒轮询、自己决定什么时候把
// 授权表投进 iframe，于是「预览」这个界面在测试里必须先有一个假后端
// （`views/demo/demoPanels.ts` 里那两条路由就是为它写的）。现在这一层在这里，展示
// 组件只认 props、只往上发事件 —— `/demo` 那条假后端照旧能喂它，因为喂的还是同一个
// fetch 层。
//
// 这一层管「拿到什么」：当前预览是哪一件、它读成的字节是什么、授权签下来没有、文档
// 那一页的字节是哪一版。用哪种查看器画、空态写哪句话、全屏按钮在不在，是画的那一半
// 的事（判据都在递下去的 props 里）。
import type { ChatAttachment, FileContent, PreviewInfo } from '../cx_types'
import type { DocumentIdentity } from '../lib/documentBytes'

import { computed, onBeforeUnmount, ref, watch } from 'vue'

import {
  attachmentRawUrl,
  downloadFile,
  getPreview,
  readPreviewFile,
  requestPreviewSession,
  uploadAttachment,
} from '../api'
import { sameDocumentIdentity, useDocumentBytes } from '../lib/documentBytes'
import { DOCUMENT_TYPES, IMAGE_SUFFIXES, isWebPage, suffixOf, webMimeOf } from '../lib/fileKind'
import { roomFileDestination } from '../lib/previewSession'

import { APP_NAVIGATION_BUDGET_MS, usePreviewFrames } from './usePreviewFrames'

import { t } from '@/i18n'

export interface PanelPreviewProps {
  topicId: string | null
  projectId: string | null
  /** This tab is the one on screen. Loads happen on the rising edge. */
  active?: boolean
  /** Bumped by WorkPanel when a turn ends — silent re-fetch, never a spinner. */
  refreshTick?: number
  /**
   * 这一格看的是房间里指定的哪一份文件（工作面板自由区的一个页签）。不给就是
   * 固定的「预览」那一格：芝士最后摆出来的那一样，要跟着它走、要轮询。给了就只
   * 看这一份，房间的当前预览换成什么都和它无关。
   */
  path?: string | null
}

export interface PanelPreviewOptions {
  /**
   * 授权表投进哪一个 iframe —— 展示组件渲染出来的那一个。POST 必须落在已经挂上的
   * frame 上：名字没对上，浏览器会开一个新标签页。
   */
  frameName: string
  /**
   * 元数据回来一次叫一次：这一格此刻指的是哪一件产物。房间拿它标「有新内容」。
   * 和以前一样，没换过的那一份也叫——判据在 WorkPanel 那一边。
   */
  onLoaded?: (artifactId: string | null) => void
  /** 帧里按了 ESC：怎么处理是画的那一半的事，这一层只往上递。 */
  onEscape?: () => void
}

/** 一张要进房间的图。上传真正要的只有这两样。 */
export interface ImageUpload {
  blob: Blob
  filename: string
}

/** 图上画完的那张合成图：展示组件把它做出来，取数这一层把它送进房间。 */
export interface AnnotateDraft extends ImageUpload {
  naturalWidth: number
  naturalHeight: number
  count: number
  note: string
}

/** 传一张图、换回一条能挂到消息上的附件。组件按 prop 拿它，自己不碰 fetch。
 *  收的只是「一张图」，因为要传的不止合成图一种：指出页面上的一点时，配图是那一页
 *  当时的样子，没有画过任何东西。 */
export type UploadAnnotation = (topicId: string, image: ImageUpload) => Promise<ChatAttachment>

/** 「预览」这一格的全部取数：状态进、动作出，一个组件都不碰。 */
export function usePanelPreview(props: PanelPreviewProps, options: PanelPreviewOptions) {
  const host = usePreviewFrames(options.frameName, { onEscape: options.onEscape })
  const loading = ref(false)
  const refreshing = ref(false)
  const previewFile = ref<FileContent | null>(null)
  const previewMime = ref('text/html')
  const previewNamed = ref(false)
  const previewUrl = ref<string | null>(null)
  const previewAppNote = ref('')
  const previewTunnelUp = ref(false)
  const previewNamedPath = ref('')
  const previewError = ref<string | null>(null)
  const previewReadError = ref<string | null>(null)
  const downloadError = ref('')
  // 路上那一次属于哪一代：话题换了、或者又按了一次刷新，先前那一次的结果就不再算数
  // （它带的是上一份内容，落下来就是「刚切换的这一格显示着上一格的东西」）。
  let generation = 0

  // ---- 这一份是什么 ----
  // 「三种查看器怎么分派」「什么时候该把字节交给 iframe」的判据都在这一小撮里，画的
  // 那一半要的也是它们，所以一并递出去，而不是两边各算一遍：一个文件是哪一种类型只能
  // 有一个答案。
  const documentSuffix = computed(() => suffixOf(previewFile.value?.path ?? ''))
  const documentType = computed(() => DOCUMENT_TYPES[documentSuffix.value] ?? null)
  const documentName = computed(() => previewFile.value?.path.split('/').pop() ?? '')
  const isImageArtifact = computed(() => IMAGE_SUFFIXES.has(documentSuffix.value))

  // ---- 指定的一份文件（自由区的页签） ----
  // 消息里的 `<&路径>` 只是一个路径，不带它在哪个库。房间自己的文件都在这里，芝士
  // 点名的当前预览也只是其中一份，所以看其中任何一份都是同一套显示，只是不跟着当前
  // 预览走。
  async function loadFile(path: string, opts: { silent?: boolean; reload?: boolean } = {}) {
    const tid = props.topicId
    if (!tid) return
    if (opts.silent && !opts.reload && (loading.value || refreshing.value)) return
    const current = ++generation
    if (opts.silent) refreshing.value = true
    else loading.value = true
    try {
      const content = await readPreviewFile(tid, path)
      if (current !== generation) return
      if (!isWebPage(path)) {
        host.reset()
        previewUrl.value = null
      }
      previewAppNote.value = ''
      previewError.value = null
      previewReadError.value = null
      previewNamed.value = true
      previewNamedPath.value = path
      if (!isWebPage(path)) previewMime.value = ''
      previewFile.value = content
      if (isWebPage(path)) await mountWebPage(tid, path, content, current, opts)
    } catch (e) {
      if (current !== generation) return
      previewFile.value = null
      previewReadError.value = e instanceof Error ? e.message : t('work.room.preview.fileReadFailed')
    } finally {
      if (current === generation) {
        loading.value = false
        refreshing.value = false
      }
    }
  }

  // 房间里的一份网页：它和「当前预览」走的是同一条路——内容域 + 沙箱 iframe——只是
  // destination 指这一份文件，不指房间当前产出的那一样。内容域按路径服务它，所以页面
  // 里的相对资源（`./style.css`）正好落在文件自己旁边。
  //
  // 网页不由展示组件渲染：`readPreviewFile` 拿回来的只有那几行源码，挂上去读者看到的
  // 是标签本身。字节交给 iframe。

  async function mountWebPage(
    tid: string,
    path: string,
    content: FileContent,
    current: number,
    opts: { silent?: boolean; reload?: boolean }
  ) {
    // This is metadata identity, not a server snapshot or entry precondition.
    // Reuse only a loaded/pending context with a known version; POST is not load.
    const identity = content.version ? JSON.stringify([tid, path, content.version]) : undefined
    if (opts.silent && !opts.reload && identity) {
      if (host.failedIdentity.value === identity) return
      if (host.incoming.value?.identity === identity) return
      if (host.displayed.value?.identity === identity && host.navigation.value !== 'failed') return
    }
    host.authorize(identity)
    let session: Awaited<ReturnType<typeof requestPreviewSession>>
    try {
      session = await requestPreviewSession(tid, { path, version: content.version })
    } catch (e) {
      // 文件读到了、只是这一次授权没签下来。说成「这个文件读不到」是假话——它读到了。
      if (current !== generation) return
      previewError.value = e instanceof Error ? e.message : t('work.room.preview.authFailed')
      host.fail(previewError.value)
      return
    }
    if (current !== generation) return
    previewMime.value = webMimeOf(suffixOf(path))
    previewUrl.value = session.url
    await host.navigate(
      session,
      {
        url: session.url,
        label: path,
        mime: previewMime.value,
        version: content.version ?? null,
        live: false,
        identity,
      },
      () => current === generation,
      roomFileDestination(path)
    )
  }

  async function load(opts: { silent?: boolean; reload?: boolean } = {}) {
    if (props.path) return loadFile(props.path, opts)
    // Metadata polling must not cancel an explicit refresh's pending grant.
    if (opts.silent && !opts.reload && (loading.value || refreshing.value)) return
    const tid = props.topicId
    const pid = props.projectId
    if (!tid || !pid) return
    const current = ++generation
    const stillCurrent = () => current === generation
    if (opts.silent) refreshing.value = true
    else loading.value = true
    try {
      let art: PreviewInfo | null
      try {
        art = await getPreview(tid)
      } catch (e) {
        if (!stillCurrent()) return
        previewUrl.value = null
        previewError.value = e instanceof Error ? e.message : t('work.room.preview.loadFailedShort')
        return
      }
      if (!stillCurrent()) return
      previewError.value = null
      previewReadError.value = null
      previewNamed.value = !!art
      previewNamedPath.value = art?.path ?? ''
      options.onLoaded?.(art?.artifact_id ?? null)
      if (!art) {
        host.reset()
        previewUrl.value = null
        previewFile.value = null
        previewAppNote.value = ''
        return
      }
      const identity = JSON.stringify([
        tid,
        art.kind ?? 'file',
        art.artifact_id ?? art.path,
        art.url,
        art.version ?? null,
        art.instance ?? null,
      ])
      const known = art.kind === 'app' || !!art.version
      const unchanged =
        known &&
        (host.incoming.value?.identity === identity ||
          (host.displayed.value?.identity === identity && host.navigation.value !== 'failed'))
      previewAppNote.value = art.kind === 'app' ? art.path : ''
      previewTunnelUp.value = !!art.tunnel_up
      if (art.kind === 'app') {
        host.observeConnection(art.instance, !!art.url && !!art.tunnel_up)
        const displayed = host.displayed.value
        if (
          !opts.reload &&
          displayed?.live &&
          displayed.instance &&
          displayed.identity &&
          JSON.parse(displayed.identity)[2] === (art.artifact_id ?? art.path)
        ) {
          return
        }
        previewFile.value = null
        if (!art.url || !art.tunnel_up) {
          previewUrl.value = null
          host.authorize(identity)
          host.fail(t('tasks.preview.unavailable'))
          return
        }
      } else {
        previewMime.value = art.mime || 'text/html'
        try {
          const content = await readPreviewFile(tid)
          if (!stillCurrent()) return
          previewFile.value = content
        } catch (e) {
          if (!stillCurrent()) return
          previewUrl.value = null
          previewFile.value = null
          previewReadError.value = e instanceof Error ? e.message : t('work.room.preview.fileReadFailed')
          return
        }
        if (!stillCurrent()) return
        // 两种「读不到文本」的情形分开走：
        // - 图片：它的内容本来就是字节，null 是正常的，交给 iframe 直接显示，
        //   否则会掉进下面那句「这个文件不是文本」——预览域本身是拿 image/*
        //   把这些字节发出来的，浏览器画得出来。
        // - 其它二进制（docx/xlsx 走 documentType 那份分支，这里指没认出来的）：
        //   没有 iframe 能显示它，停下。
        if (previewFile.value.content === null && !previewFile.value.too_large && !isImageArtifact.value) {
          host.reset()
          previewUrl.value = null
          return
        }
        // 认得出的文档类型一律由查看器画，不进 iframe。Markdown 是因为预览域按
        // text/markdown 发出来浏览器只显示源码；其余是因为「读得成文本」不代表它是
        // 网页：一份字节其实是文字的 .pdf 会被按 application/pdf 发进沙箱 iframe，
        // 浏览器画不出也不报错，面板就是一片空白。交给查看器，它会说清楚。
        if (documentType.value) {
          host.reset()
          previewUrl.value = null
          return
        }
      }
      if (!opts.reload && (unchanged || (opts.silent && host.failedIdentity.value === identity))) return
      if (!art.url) {
        previewUrl.value = null
        previewError.value = t('work.room.preview.urlUnavailable')
        return
      }
      try {
        host.authorize(identity)
        const session = await requestPreviewSession(
          tid,
          art.kind === 'app'
            ? art.instance
              ? { artifact_id: art.artifact_id, instance: art.instance }
              : undefined
            : { artifact_id: art.artifact_id, version: art.version, path: art.path }
        )
        if (!stillCurrent()) return
        previewUrl.value = art.url
        await host.navigate(
          session,
          {
            url: art.url,
            label: art.path,
            mime: art.mime || 'text/html',
            version: art.version ?? null,
            live: art.kind === 'app',
            identity,
            // 应用可能正赶上机器冷启动，那 30 秒的默认档会把它误报成超时。
            budgetMs: art.kind === 'app' ? APP_NAVIGATION_BUDGET_MS : undefined,
          },
          stillCurrent
        )
      } catch (e) {
        if (!stillCurrent()) return
        if (!host.displayed.value) previewUrl.value = null
        previewError.value = e instanceof Error ? e.message : t('work.room.preview.authFailed')
        host.fail(previewError.value)
      }
    } finally {
      if (stillCurrent()) {
        loading.value = false
        refreshing.value = false
      }
    }
  }

  watch(
    () => props.active,
    (active) => {
      if (active) void load({ silent: !!previewUrl.value })
      else {
        generation += 1
        host.pause()
        loading.value = false
        refreshing.value = false
        if (!host.displayed.value) {
          previewUrl.value = null
        }
      }
    },
    { immediate: true }
  )
  watch(
    () => [props.topicId, props.path],
    () => {
      generation += 1
      host.reset()
      previewUrl.value = null
      previewFile.value = null
      previewError.value = null
      previewReadError.value = null
      previewNamed.value = false
      previewAppNote.value = ''
      loading.value = false
      refreshing.value = false
      if (props.active) void load()
    }
  )
  watch(
    () => props.refreshTick,
    () => {
      if (props.active) void load({ silent: true })
    }
  )

  // Poll metadata only; an unchanged artifact never receives a new form POST.
  let refreshTimer: ReturnType<typeof setInterval> | null = null
  function stopAutoRefresh() {
    if (refreshTimer) clearInterval(refreshTimer)
    refreshTimer = null
  }
  watch(
    () => props.active,
    (active) => {
      stopAutoRefresh()
      // 指定了文件的那一格不轮询：它不跟着当前预览走，文件变了靠每一轮收工那一下重读。
      // 页面在后台的那一格也不问：后台标签的轮询是白花的，回到前台自然会重取。
      if (!active || props.path) return
      refreshTimer = setInterval(() => {
        if (!document.hidden) void load({ silent: true })
      }, 20_000)
    },
    { immediate: true }
  )
  onBeforeUnmount(() => {
    generation += 1
    stopAutoRefresh()
  })

  // 下载当前这一份。地址由 api 那一层拼（房间里的文件走 attachment 那条路），文件名
  // 取路径最后那一段——判据都在这一层，所以展示组件只往上发一个「下载」。
  async function downloadArtifact() {
    downloadError.value = ''
    const path = previewFile.value?.path
    if (!props.topicId || !path) return
    try {
      await downloadFile(attachmentRawUrl(props.topicId, path), documentName.value || 'file')
    } catch (e) {
      downloadError.value = e instanceof Error ? e.message : t('work.room.preview.downloadFailed')
    }
  }

  // ---- 文档字节 ----
  // 那一页的字节由 `useDocumentBytes` 取：浏览器画不出来的先转 PDF，其余读原始字节。
  // 「改动」那一格取的是同一份东西，所以这件事只写在一处。
  const docNonce = ref(0)
  const docIdentity = computed<DocumentIdentity | null>(() => {
    const file = previewFile.value
    if (!props.topicId || !file) return null
    return {
      topicId: props.topicId,
      path: file.path,
      taskId: null,
      source: file.source ?? 'live',
      version: file.version,
    }
  })
  const {
    bytes: docBytes,
    snapshot: docSnapshot,
    loading: docLoading,
    error: docError,
    rendererMissing: docRendererMissing,
  } = useDocumentBytes({
    topicId: () => docIdentity.value?.topicId ?? null,
    path: () => docIdentity.value?.path ?? null,
    version: () => docIdentity.value?.version ?? null,
    task: () => docIdentity.value?.taskId ?? null,
    source: () => docIdentity.value?.source ?? 'live',
    nonce: () => docNonce.value,
    enabled: () =>
      (!!documentType.value && documentType.value.view !== 'markdown') || (!!props.path && isImageArtifact.value),
  })
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
   * 处理完一处修订、关掉在线编辑器、恢复了一版草稿：文件都变了，而这一页的字节是按
   * 文件版本缓存的，版本却没变（是这里改的，不是芝士改的），所以自己打一下让它重取。
   */
  function refreshDocument() {
    docNonce.value += 1
  }

  /**
   * 标注合成图要进房间：上传是取数这一层的事。
   *
   * 附件路径是上传给的，消息得等它回来才拼得出来，所以这一步交给这一层、由画的那
   * 一半按 prop 调；origin 用 clipboard —— 它是那句话的配图，不是一份要进资料库供
   * 人浏览的文档。
   */
  async function uploadAnnotation(topicId: string, image: ImageUpload): Promise<ChatAttachment> {
    const file = new File([image.blob], image.filename, { type: 'image/png' })
    return uploadAttachment(topicId, file, 'clipboard')
  }

  return {
    // 这一格现在画的是什么
    frames: host.frames,
    displayedFrame: host.displayed,
    navigation: host.navigation,
    navigationError: host.error,
    frameLoaded: host.loaded,
    frameFailed: host.failed,
    loading,
    refreshing,
    previewFile,
    previewMime,
    previewNamed,
    previewUrl,
    previewAppNote,
    previewTunnelUp,
    previewNamedPath,
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
  }
}
