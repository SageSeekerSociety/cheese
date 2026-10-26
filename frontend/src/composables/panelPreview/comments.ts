// 预览这一格上的评论：这一份文件上有哪些线程、写一条新的、回复和解决。
//
// 评论挂的是「房间里的这份文件」（`useFileThreads` 按房间 + 路径取数），不是预览
// 台上此刻摆着的那一件事——同一份文件从哪个页签打开，都是同一串评论。
//
// 发一条新评论时，锚点是读者此刻指着的那一处（预览里已有的一套指认）；没指就是
// 整份文件。发完把线程读回来，别的就交给服务端。
import type { QuotedContext } from '../../lib/quotedContext'
import type { DocPeopleBundle } from '../useDocPeople'
import type { PreviewState } from './previewState'
import type { PanelPreviewProps } from './types'

import { computed, ref, watch } from 'vue'

import { postFileThread } from '../../api/fileThreads'
import { SLIDE_SUFFIXES } from '../../lib/fileKind'
import { myHandle } from '../../me'
import { useFileThreads } from '../useFileThreads'

export interface CommentsDeps {
  props: PanelPreviewProps
  state: PreviewState
  /** 名册那一包（`useDocPeople`）：卡上的名字和头像从它来，和文档那一栏同一处。 */
  people: DocPeopleBundle
  /** 屏幕上这一份是不是读成了网页（`docPage`）。网页那一档画不出标记（它在沙箱帧里），
   *  所以栏关着时不必为它读评论串。 */
  docPage: () => boolean
}

/** 一份文件上的评论串，和写一条新的那条路。 */
export function usePanelPreviewComments(deps: CommentsDeps) {
  const { props, state, people } = deps
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
  // 这一栏开着没有。关着的时候不问接口——预览每一格打开都多打一次，不值。
  // 例外是画标记的那一档，见下面 `drawsMarks`：那一档的数据栏关着也得有。
  const open = ref(false)
  function toggle() {
    open.value = !open.value
  }
  // 这一份不能挂评论了（换到资料库原件、消息附件，或者回到还没有文件的格子）：
  // 那一栏跟着收起来，别让它以打开的姿态留到下一份上。
  watch(commentable, (current) => {
    if (!current) open.value = false
  })
  // 要画标记的那几档：这一份的线程列表在栏关着时也得读。标记是从线程列表算出来的，
  // 等读者自己去点开那一栏才读，「哪儿有人评过」这件事就要他先想到去开栏才看得见——
  // 而那正是标记要替他省掉的一步。
  //
  // 哪几档画标记：markdown 画在正文上（`PreviewMarkdown`），转成 PDF 的文档画在页上
  // （`PreviewPages`），表格画在格子上（`PreviewSheet`），图片画在图上（`PreviewImageStage`）。
  // 别的读法有了标记时，这里跟着加。
  // 这一条和 `PanelPreviewView` 里 `liveAnchors` 收哪几种锚点是配对的——那边多收一种，
  // 这边就得跟着多读一种，不然标记一个都不会出现（栏关着的时候列表根本没读）。
  //
  // 幻灯片要单独排掉：它也是 `view === 'pages'`，但页上画不出标记（评的是整页）——
  // 圈进来就是每打开一份 pptx 都白打一次评论接口。
  const drawsMarks = computed(() => {
    const view = state.documentType.value?.view
    if (commentable.value === null) return false
    if (view === 'markdown') return true
    // 图片：这一档没有 `documentType` 那种「读法」，是看后缀认出来的；它上屏的条件和
    // 宿主里挂 `PreviewImageStage` 那一条一样——指定了文件、内容是字节、后缀是图片。
    if (state.isImageArtifact.value && !!props.path && !!state.previewFile.value) return true
    // 读成网页时画的是沙箱帧，标记画不上去（`PreviewDocStage` 那一支挂的是 `PreviewPage`）。
    // 表格也算在内：`.xlsx` 有网页这一种读法（`PAGE_FORMATS` 认它），切过去之后挂的是
    // 网页那一支，表格查看器根本没上屏——这里先放行再排掉，等于每打开一份这样的表
    // 白打一次评论接口。
    if (deps.docPage()) return false
    if (view === 'sheet') return true
    return view === 'pages' && !SLIDE_SUFFIXES.has(state.documentSuffix.value)
  })
  const threads = useFileThreads(
    () => commentable.value?.topicId ?? null,
    () => commentable.value?.path ?? null,
    () => open.value || drawsMarks.value
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
    /** 卡上写作者名字：名册上他是谁，名册上没有他的名字就写 handle。 */
    nameOf: (handle: string) => people.names.value[handle] ?? handle,
    /** 卡上画作者头像：他挑过的那个；没挑过、或不在名册上时给空串，由 UserAvatar
     *  画彩色首字母——和文档评论栏同一条规矩，不退回全站默认脸。 */
    avatarOf: (handle: string) => people.avatars.value[handle] ?? '',
  }
}

/** 这一包递给画的那一层（props）：取数在这一层，面板不再自己去取。 */
export type PreviewCommentsBundle = ReturnType<typeof usePanelPreviewComments>
