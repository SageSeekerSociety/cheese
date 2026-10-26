// 在预览格里和几个人一起改同一份文本房间文件（markdown）。
//
// 这是「概览文档那套协同」在房间文件上的同一套：正文交给协作服务（Yjs），谁打的字
// 都实时合进同一文档，服务在停手几秒后把它存回去——存成的仍是这份文件自己的新一版，
// 预览台、历史、还原读的都是那份文件。
//
// 取数那一侧是 `useDocCollab`：它本来只认「按文档 id 要票」这一种开法（活文档），
// 这里递给它一个 `DocLiveSource`（按房间 + 路径要票，拿回的名字是 `roomfile:<id>`）。
// 连接、重连、别人的光标、存回的计数都由它负责，这一层只管「哪一份开着」和「屏幕上
// 那几个状态怎么读」。
import type { ComputedRef, Ref } from 'vue'
import type { DocConnection, DocPeer, DocSession } from '../useDocCollab'
import type { PreviewState } from './previewState'
import type { PanelPreviewProps } from './types'

import { computed, ref, watch } from 'vue'

import type { DocTicket } from '@/api/docCollab'
import { openRoomFileLive } from '@/api/roomFileCollab'
import { t } from '@/i18n'

import { useDocCollab, type DocLiveSource } from '../useDocCollab'

export interface LiveEditDeps {
  props: PanelPreviewProps
  state: PreviewState
  /** 存回了一版：让这一页重读那份文件（评论锚点、历史、预览台上那一份都跟着换）。 */
  refreshDocument: () => void
}

/** 这一格递下去画的东西：能否开协同编辑、开着的是哪一份、连接怎么样。 */
export interface PanelPreviewLiveEdit {
  /** 现在这一份能不能在预览格里和大家同编。 */
  canEdit: ComputedRef<boolean>
  /** 正开着的那一份的路径；没开着时是空串（用于横条上的标题）。 */
  editingPath: ComputedRef<string>
  /** 这一份打不开（二进制、太大、部署没接协作服务）：一句给读者看的话；空串是能开。 */
  refused: Ref<string>
  /** 打开着的这一份，交给编辑器绑在它上面；还没连上时是 null。 */
  session: Ref<DocSession | null>
  connection: Ref<DocConnection>
  /** 连上了、正文到过一次。 */
  synced: Ref<boolean>
  /** 只读：服务端照它拒写。 */
  readOnly: Ref<boolean>
  peers: Ref<DocPeer[]>
  /** 打开失败（网络、读不出来的那一份）：一句给读者看的话；空串是没有。 */
  error: Ref<string>
  /** 页面用的文档 schema 和服务对不上：得刷新。 */
  outdated: Ref<boolean>
  /** 这一份已经被删了。 */
  deleted: Ref<boolean>
  /** 开始编辑当前这一份。 */
  begin: () => Promise<void>
  /** 收起编辑。 */
  end: () => void
}

/** 一份文本房间文件的协同编辑会话。 */
export function usePanelPreviewLiveEdit(deps: LiveEditDeps): PanelPreviewLiveEdit {
  const { props, state, refreshDocument } = deps

  /** 现在开着协同编辑的那一份（房间 + 路径钉在打开的那一刻）。 */
  const pinned = ref<{ room: string; path: string } | null>(null)
  const editingPath = computed(() => pinned.value?.path ?? '')
  /** 打开时后端说的话：开不成的理由（给读者看的一句人话）。 */
  const refused = ref('')
  /** 那一句说的是哪一份文件：切到别的文件时它就不作数了。 */
  const refusedPath = ref('')
  const error = ref('')
  /** `begin()` 已经拿到的那张票；连接首次开口就用它，不必再要一遍。 */
  let first: DocTicket | null = null

  const source: DocLiveSource = {
    // 开着的是哪一份：`房间\u0000路径` 变了就重开。关着时给 null。
    watch: () => (pinned.value ? `${pinned.value.room}\u0000${pinned.value.path}` : null),
    open: async () => {
      // 首次开口用 `begin()` 已经要来的那张票；重连时才重新要（票只活两分钟）。
      if (first) {
        const use = first
        first = null
        return use
      }
      const target = pinned.value
      if (!target) throw new Error(t('work.room.preview.fileReadFailed'))
      const live = await openRoomFileLive(target.room, target.path)
      if (!live.enabled || !live.document || live.ticket === undefined) {
        throw new Error(refusalText(live.reason))
      }
      return { document: live.document, ticket: live.ticket, read_only: !!live.read_only, suggest_only: false }
    },
  }

  const collab = useDocCollab(() => null, source)

  // 存回了一版：让这一页重读，评论锚点、历史、别的格子里那一份都跟着换。
  watch(collab.stores, () => {
    if (pinned.value) refreshDocument()
  })
  // 服务说这一份被删了：编辑没什么可留在屏幕上了，收起来。
  watch(collab.deleted, (gone) => {
    if (gone) end()
  })

  // 屏幕上那份文件换掉了（切到别的文件、芝士换了当前预览），或者换了房间：收起。
  // 只在真的换了另一份时收一次——`previewFile` 会被静默轮询置空，那不是「换了一份」。
  // 上一份「开不了」的提示也在这时候清掉：它说的是另一份文件的事。
  watch(
    () => [state.previewFile.value?.path ?? '', props.topicId ?? ''] as const,
    ([path, room]) => {
      const target = pinned.value
      if (target && ((path && path !== target.path) || room !== target.room)) end()
      else if (path && path !== refusedPath.value) refused.value = ''
    }
  )

  /** 能在预览格里同编的：是 markdown、是这个房间的文件、正文读得出来。
   *
   *  判据和另一边（`textEdit` 那一套的旧判据）同源：`content` 不是 null（二进制、太大
   *  的读不出正文），不是资料库原件或消息附件（那两样不是这个房间的文件），并且已经有
   *  不为空的版本（文件在，且读的时候它的版本拿得到）。这样「编辑」按钮不会摆在一个
   *  后端必然拒的文件上。 */
  const canEdit = computed(() => {
    const file = state.previewFile.value
    if (!file || !props.topicId) return false
    if (file.content === null) return false
    if (!file.version) return false
    if (file.binary || file.too_large) return false
    if (file.path.startsWith('library/') || file.path.startsWith('uploads/')) return false
    return state.documentType.value?.view === 'markdown'
  })

  async function begin() {
    const file = state.previewFile.value
    const room = props.topicId
    // 已经开着：这一下按的是同一个「编辑」，不再开一遍（重开会把这张票换掉）。
    if (pinned.value || !room || !file) return
    error.value = ''
    refused.value = ''
    try {
      const live = await openRoomFileLive(room, file.path)
      if (!live.enabled || !live.document || live.ticket === undefined) {
        // 开不成：把理由摆出来，不挂一条连不上的会话。
        refused.value = refusalText(live.reason)
        refusedPath.value = file.path
        return
      }
      first = { document: live.document, ticket: live.ticket, read_only: !!live.read_only, suggest_only: false }
      pinned.value = { room, path: file.path }
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : t('work.room.preview.fileReadFailed')
    }
  }

  function end() {
    pinned.value = null
    first = null
    refused.value = ''
    refusedPath.value = ''
    error.value = ''
    // 连接由 `useDocCollab` 看着 watch 的结果自己收。
  }

  return {
    canEdit,
    editingPath,
    refused,
    session: collab.session,
    connection: collab.connection,
    synced: collab.synced,
    readOnly: collab.readOnly,
    peers: collab.peers,
    error,
    outdated: collab.outdated,
    deleted: collab.deleted,
    begin,
    end,
  }
}

function refusalText(reason: string | undefined): string {
  return reason ? t(`work.room.fileEditor.unavailable.${reason}`) : t('work.room.preview.fileReadFailed')
}
