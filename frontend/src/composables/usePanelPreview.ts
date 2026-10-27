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
//
// 这一份是组合根：这一格摆着的是哪一件（`panelPreview/previewState.ts`）、房间里当前
// 那件产物怎么挂上去（`currentPreview.ts`）、指定的那一份文件怎么读（`namedFile.ts`）、
// 文档的字节与两种读法（`documentBytes.ts`）、在线编辑与历史（`editing.ts`）、元数据的
// 轮询（`polling.ts`）各自成篇拆在 `panelPreview/` 下。返回的那一包和拆之前一模一样，
// 组件、演示夹具与单测都不用动。
import type { McpAppMessage } from '@/lib/mcpAppBridge'
import type { ChatAttachment } from '../cx_types'
import type { ImageUpload, PanelPreviewOptions, PanelPreviewProps } from './panelPreview/types'

import { onBeforeUnmount, ref, watch } from 'vue'

import { attachmentRawUrl, downloadFile, uploadAttachment } from '../api'

import { usePanelPreviewAppSleep } from './panelPreview/appWake'
import { usePanelPreviewComments } from './panelPreview/comments'
import { useCurrentPreview } from './panelPreview/currentPreview'
import { usePanelPreviewDocument } from './panelPreview/documentBytes'
import { usePanelPreviewEditing } from './panelPreview/editing'
import { usePanelPreviewLiveEdit } from './panelPreview/liveEdit'
import { usePanelNamedFile } from './panelPreview/namedFile'
import { usePreviewPolling } from './panelPreview/polling'
import { createPreviewState } from './panelPreview/previewState'
import { useDocPeople } from './useDocPeople'
import { usePreviewFrames } from './usePreviewFrames'

import { t } from '@/i18n'

export type {
  AnnotateDraft,
  ImageUpload,
  PanelPreviewOptions,
  PanelPreviewProps,
  UploadAnnotation,
} from './panelPreview/types'

/** 「预览」这一格的全部取数：状态进、动作出，一个组件都不碰。 */
export function usePanelPreview(props: PanelPreviewProps, options: PanelPreviewOptions) {
  // 帧的托管：授权表投进哪一帧、帧报到什么状态，都在它那儿。
  /** 第三方 View 里的表单点提交（`ui/message`）：拼成一条普通提问往上递。 */
  function submitAppMessage(message: McpAppMessage) {
    const url = frames.displayed.value?.url
    const topicId = props.topicId
    if (!url || !topicId || !message.text.trim()) return
    options.onAppMessage?.({
      topicId,
      content: message.text,
      intent: 'ask-agent',
      quotedContext: {
        kind: 'web-region',
        url,
        rect: { x: 0, y: 0, w: 0, h: 0 },
        viewport: { w: Math.max(1, window.innerWidth), h: Math.max(1, window.innerHeight) },
      },
    })
  }
  const frames = usePreviewFrames(options.frameName, {
    onEscape: options.onEscape,
    onPick: options.onPick,
    onAppMessage: submitAppMessage,
    // 应用那一帧拍的快照交给睡眠那一层存（它要用到下面才建的状态，所以晚一步接上）。
    onSnapshot: (frame, shot) => sleep.keep(frame, shot),
  })
  const state = createPreviewState()
  const named = usePanelNamedFile({ props, state, frames })
  const target = useCurrentPreview({ props, options, state, frames, loadNamed: named.loadFile })
  const load = target.load
  // 应用睡着时的快照和唤醒（`panelPreview/appWake.ts`）。
  const sleep = usePanelPreviewAppSleep({
    props,
    state,
    displayed: () => frames.displayed.value,
    reload: () => load({ silent: true, reload: true }),
  })

  watch(
    () => props.active,
    (active) => {
      if (active) void load({ silent: !!state.previewUrl.value })
      else {
        state.invalidate()
        frames.pause()
        state.loading.value = false
        state.refreshing.value = false
        state.autoReloaded.value = false
        target.resetFollow()
        if (!frames.displayed.value) {
          state.previewUrl.value = null
        }
      }
    },
    { immediate: true }
  )
  watch(
    () => [props.topicId, props.path],
    () => {
      state.invalidate()
      frames.reset()
      state.previewUrl.value = null
      state.previewFile.value = null
      state.previewError.value = null
      state.previewReadError.value = null
      state.previewNamed.value = false
      state.previewAppNote.value = ''
      state.autoReloaded.value = false
      target.resetFollow()
      state.loading.value = false
      state.refreshing.value = false
      if (props.active) void load()
    }
  )
  watch(
    () => props.refreshTick,
    () => {
      if (props.active) void load({ silent: true })
    }
  )
  // 那一句「已自动重新载入」只在跟上去的那一帧装成之前成立：装好了（页面上已经是
  // 重启后的应用）或没装成（面板另有报错），它就该走了。
  watch(frames.navigation, (nav) => {
    if (nav === 'loaded' || nav === 'failed') state.autoReloaded.value = false
  })

  usePreviewPolling({ props, frames, load })

  onBeforeUnmount(() => state.invalidate())

  // 下载当前这一份。地址由 api 那一层拼（房间里的文件走 attachment 那条路），文件名
  // 取路径最后那一段——判据都在这一层，所以展示组件只往上发一个「下载」。
  const downloadError = ref('')
  async function downloadArtifact() {
    downloadError.value = ''
    const path = state.previewFile.value?.path
    if (!props.topicId || !path) return
    try {
      await downloadFile(attachmentRawUrl(props.topicId, path), state.documentName.value || 'file')
    } catch (e) {
      downloadError.value = e instanceof Error ? e.message : t('work.room.preview.downloadFailed')
    }
  }

  const doc = usePanelPreviewDocument({ props, state })
  // 这一份文件上的评论：线程、写一条新的、回复和解决。
  //
  // 名册那一包（`useDocPeople`）先建：评论卡上的名字和头像从它来，和文档评论栏
  // 同一处、同一份实现——不是预览这一格自己再算一遍。不接它的话卡上会显示 handle
  // 加色块（那曾经是个静默的 bug）。
  const people = useDocPeople({
    members: () => props.members ?? [],
    agentHandle: () => props.agentHandle ?? null,
    agentName: () => props.agentName ?? '',
  })
  const comments = usePanelPreviewComments({ props, state, people, docPage: () => doc.docPage.value })
  const edits = usePanelPreviewEditing({
    props,
    state,
    onOpenFile: options.onOpenFile,
    refreshDocument: doc.refreshDocument,
  })
  // 在预览格里和几个人一起改一份 markdown：正文走协作服务，存回那一份房间文件本身
  // （Office 那几种走 OnlyOffice，文本这一条不经过它）。
  const liveEdit = usePanelPreviewLiveEdit({
    props,
    state,
    refreshDocument: doc.refreshDocument,
  })

  /**
   * 标注合成图要进房间：上传是取数这一层的事。
   *
   * 附件路径是上传给的，消息得等它回来才拼得出来，所以这一步交给这一层、由画的那
   * 一半按 prop 调。
   */
  async function uploadAnnotation(topicId: string, image: ImageUpload): Promise<ChatAttachment> {
    const file = new File([image.blob], image.filename, { type: 'image/png' })
    return uploadAttachment(topicId, file)
  }

  return {
    // 这一格现在画的是什么
    frames: frames.frames,
    displayedFrame: frames.displayed,
    navigation: frames.navigation,
    navigationError: frames.error,
    frameLoaded: frames.loaded,
    frameFailed: frames.failed,
    loading: state.loading,
    refreshing: state.refreshing,
    previewFile: state.previewFile,
    previewMime: state.previewMime,
    previewNamed: state.previewNamed,
    previewUrl: state.previewUrl,
    previewAppNote: state.previewAppNote,
    previewTunnelUp: state.previewTunnelUp,
    previewAppUp: state.previewAppUp,
    previewNamedPath: state.previewNamedPath,
    autoReloaded: state.autoReloaded,
    previewError: state.previewError,
    previewReadError: state.previewReadError,
    documentSuffix: state.documentSuffix,
    documentType: state.documentType,
    documentName: state.documentName,
    isImageArtifact: state.isImageArtifact,
    downloadError,
    docBytes: doc.docBytes,
    docIdentity: doc.docIdentity,
    docSnapshot: doc.docSnapshot,
    slideContext: doc.slideContext,
    docLoading: doc.docLoading,
    docError: doc.docError,
    docRendererMissing: doc.docRendererMissing,
    // 递下去的是「这一份现在读成网页」，不是读者手上那个开关（见 `panelPreview/documentBytes.ts`）。
    docPage: doc.docPage,
    canPage: doc.canPage,
    docPageHtml: doc.docPageHtml,
    // 这一份文件上的评论（同上，原样递下去）
    comments,
    // 在线编辑 + 历史（各自的取数那一包，原样递下去）
    revs: edits.revs,
    editing: edits.editing,
    showHistory: edits.showHistory,
    editor: edits.editor,
    fileHistory: edits.fileHistory,
    // 在预览格里直接改一份 markdown（存回房间文件本身）
    liveEdit,
    // 动作
    load,
    downloadArtifact,
    refreshDocument: doc.refreshDocument,
    uploadAnnotation,
    openEditor: edits.openEditor,
    closeEditor: edits.closeEditor,
    toggleHistory: edits.toggleHistory,
    toggleDocPage: doc.toggleDocPage,
    setDocNarrow: doc.setNarrow,
    // 圈选：画的那一半按帧的类型选「递进帧」还是「宿主自己盖一层」，取数这一层只管把
    // 开关送到当前那一帧的桥。
    setPickMode: frames.setPickMode,
    // 小浏览器：后退、前进、重载帧里那一页，跳到地址栏里敲的地址（帧里的桥执行）。
    stepHistory: frames.stepHistory,
    goTo: frames.goTo,
    // 应用睡着时：最近一次的快照、唤醒的分步进度
    appSnapshot: sleep.appSnapshot,
    wakeSteps: sleep.wakeSteps,
    wakeError: sleep.wakeError,
    wakeApp: sleep.wake,
  }
}

/** 「预览」这一格的取数原样递给面板（props）：面板自己不认识接口。 */
export type PanelPreviewBundle = ReturnType<typeof usePanelPreview>
