// 「预览」这一格里指定的那一份文件（工作面板自由区的一个页签）：读它的字节。
//
// 消息里的 `<&路径>` 只是一个路径，不带它在哪个库。房间自己的文件都在这里，芝士点名的当前
// 预览也只是其中一份，所以看其中任何一份都是同一套显示，只是不跟着当前预览走。
//
// 房间里的一份网页和「当前预览」走的是同一条路——内容域 + 沙箱 iframe——只是 destination
// 指这一份文件，不指房间当前产出的那一样。内容域按路径服务它，所以页面里的相对资源
// （`./style.css`）正好落在文件自己旁边。
//
// 网页不由展示组件渲染：`readPreviewFile` 拿回来的只有那几行源码，挂上去读者看到的是标签
// 本身。字节交给 iframe。
import type { FileContent } from '../../cx_types'
import type { PreviewState } from './previewState'
import type { PanelPreviewProps, PreviewFrames } from './types'

import { readPreviewFile, requestPreviewSession } from '../../api'
import { isWebPage, suffixOf, webMimeOf } from '../../lib/fileKind'
import { roomFileDestination } from '../../lib/previewSession'

import { t } from '@/i18n'

export interface NamedFileDeps {
  props: PanelPreviewProps
  /** 这一格摆着的那一份：读回来的字节写在这上面。 */
  state: PreviewState
  /** 帧的托管：内容域 + 沙箱 iframe 那一层。 */
  frames: PreviewFrames
}

/** 指定的一份文件的取数：读字节、是网页就挂上去。 */
export function usePanelNamedFile(deps: NamedFileDeps) {
  const { props, state, frames: host } = deps

  async function loadFile(path: string, opts: { silent?: boolean; reload?: boolean } = {}) {
    const tid = props.topicId
    if (!tid) return
    if (opts.silent && !opts.reload && (state.loading.value || state.refreshing.value)) return
    const current = state.begin()
    const stillCurrent = () => state.stillCurrent(current)
    if (opts.silent) state.refreshing.value = true
    else state.loading.value = true
    try {
      const content = await readPreviewFile(tid, path)
      if (!stillCurrent()) return
      if (!isWebPage(path)) {
        host.reset()
        state.previewUrl.value = null
      }
      state.previewAppNote.value = ''
      state.previewError.value = null
      state.previewReadError.value = null
      state.previewNamed.value = true
      state.previewNamedPath.value = path
      if (!isWebPage(path)) state.previewMime.value = ''
      state.previewFile.value = content
      if (isWebPage(path)) await mountWebPage(tid, path, content, current, opts)
    } catch (e) {
      if (!stillCurrent()) return
      state.previewFile.value = null
      state.previewReadError.value = e instanceof Error ? e.message : t('work.room.preview.fileReadFailed')
    } finally {
      if (stillCurrent()) {
        state.loading.value = false
        state.refreshing.value = false
      }
    }
  }

  async function mountWebPage(
    tid: string,
    path: string,
    content: FileContent,
    current: number,
    opts: { silent?: boolean; reload?: boolean }
  ) {
    const stillCurrent = () => state.stillCurrent(current)
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
      if (!stillCurrent()) return
      state.previewError.value = e instanceof Error ? e.message : t('work.room.preview.authFailed')
      host.fail(state.previewError.value)
      return
    }
    if (!stillCurrent()) return
    state.previewMime.value = webMimeOf(suffixOf(path))
    state.previewUrl.value = session.url
    await host.navigate(
      session,
      {
        url: session.url,
        label: path,
        mime: state.previewMime.value,
        version: content.version ?? null,
        live: false,
        identity,
      },
      stillCurrent,
      roomFileDestination(path)
    )
  }

  return { loadFile }
}
