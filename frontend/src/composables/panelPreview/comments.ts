// 预览这一格上的评论：这一份文件上有哪些线程、写一条新的、回复和解决。
//
// 评论挂的是「房间里的这份文件」（`useFileThreads` 按房间 + 路径取数），不是预览
// 台上此刻摆着的那一件事——同一份文件从哪个页签打开，都是同一串评论。
//
// 发一条新评论时，锚点是读者此刻指着的那一处（预览里已有的一套指认）；没指就是
// 整份文件。发完把线程读回来，别的就交给服务端。
import type { QuotedContext } from '../../lib/quotedContext'
import type { PreviewState } from './previewState'
import type { PanelPreviewProps } from './types'

import { computed, ref } from 'vue'

import { postFileThread } from '../../api/fileThreads'
import { myHandle } from '../../me'
import { useFileThreads } from '../useFileThreads'

export interface CommentsDeps {
  props: PanelPreviewProps
  state: PreviewState
}

/** 一份文件上的评论串，和写一条新的那条路。 */
export function usePanelPreviewComments(deps: CommentsDeps) {
  const { props, state } = deps
  // 资料库原件和消息附件都不是这个房间的文件，评论不挂在它们上面。
  const commentable = computed(() => {
    const file = state.previewFile.value
    if (!file || !props.topicId) return null
    const path = file.path
    // 资料库原件（`library/`）和一条消息的附件（`uploads/`）不是这个房间的文件：
    // 前者只读，后者属于那条消息，评论都不挂在它们上面。
    if (path.startsWith('library/') || path.startsWith('uploads/')) return null
    return { topicId: props.topicId, path, version: file.version }
  })
  // 这一栏开着没有：关着就不去问接口——预览每一格打开都多打一次，不值。
  const open = ref(false)
  function toggle() {
    open.value = !open.value
  }
  const threads = useFileThreads(
    () => commentable.value?.topicId ?? null,
    () => commentable.value?.path ?? null,
    () => open.value
  )

  /** 写一条新的：说的是什么、指着哪一处。 */
  async function post(payload: { content: string; quote: string | null; anchor: QuotedContext | null }) {
    const target = commentable.value
    if (!target) return
    await postFileThread(target.topicId, {
      path: target.path,
      content: payload.content,
      quote: payload.quote,
      anchor: payload.anchor,
    })
    await threads.refresh()
  }

  return {
    /** 这一份能不能挂评论：能就是它的地址与版本，不能是 null。 */
    target: commentable,
    /** 这一栏开着没有；关着时不读接口。 */
    open,
    toggle,
    author: myHandle(),
    threads,
    post,
  }
}

/** 这一包递给画的那一层（props）：取数在这一层，面板不再自己去取。 */
export type PreviewCommentsBundle = ReturnType<typeof usePanelPreviewComments>
