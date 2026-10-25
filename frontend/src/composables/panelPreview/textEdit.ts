// 在预览格里直接改一份文本文件（markdown），存回那一份本身。
//
// 和概览文档那种多人实时协同不是一回事：那套走协作服务（Yjs），房间文件是磁盘上的
// 字节。这里做的是「能改、存得住、冲突不丢」——读的时候拿到一个内容版本，存的时候
// 带回去；文件在那之后被别人改过，后端答 409，读到的和别人的都不会被盖掉。
import type { ComputedRef, Ref } from 'vue'
import type { PreviewState } from './previewState'
import type { PanelPreviewProps } from './types'

import { computed, ref } from 'vue'

import { ApiError } from '@/api'
import { writeRoomTextFile } from '@/api/roomFiles'
import { t } from '@/i18n'

export interface TextEditDeps {
  props: PanelPreviewProps
  state: PreviewState
  /** 存下去之后让这一页重读：换了内容，预览台上那份也得跟上。 */
  refreshDocument: () => void
}

/** 这一包递下去画的东西：外壳调一次，几个子组件只读它的形状。 */
export interface PanelPreviewTextEdit {
  /** 编辑器里现在是什么；没开着（或这一份不可编辑）时 null。 */
  draft: Ref<string | null>
  error: Ref<string>
  saving: Ref<boolean>
  conflicted: Ref<boolean>
  canEdit: ComputedRef<boolean>
  begin: () => void
  cancel: () => void
  save: () => Promise<boolean>
  /** 编辑器里打了一个字：草稿跟着换（画的组件通过它，不直接改递下去的 prop）。 */
  setDraft: (value: string) => void
}

/** 一份文本文件的编辑会话。 */
export function usePanelPreviewTextEdit(deps: TextEditDeps): PanelPreviewTextEdit {
  const { props, state, refreshDocument } = deps

  /** 现在开着的编辑器里是什么；没开（或这一份不可编辑）时是 null。 */
  const draft = ref<string | null>(null)
  /** 载入、保存各自说得出自己在做什么；空串是「没有话说」。 */
  const error = ref('')
  const saving = ref(false)
  /** 别人先改过：存不下，得让读者先看看最新的。 */
  const conflicted = ref(false)

  /** 这一份能不能在预览里直接改：是文本、是这个房间的文件、正文读得出来。
   *
   *  判据挑得很明白：`content` 不是 null（二进制/太大的读不出版本就写不回去），
   *  不是资料库原件或消息附件（那两样不是这个房间的文件，写进去别人读到的会是同名
   *  的另一份——后端也拒），并且已经有不为空的版本（文件在，且读的时候它的版本
   *  拿得到）。 */
  const canEdit = computed(() => {
    const file = state.previewFile.value
    if (!file || !props.topicId) return false
    if (!file.content) return false
    if (!file.version) return false
    if (file.binary || file.too_large) return false
    const path = file.path
    if (path.startsWith('library/') || path.startsWith('uploads/')) return false
    return state.documentType.value?.view === 'markdown'
  })

  function begin() {
    const file = state.previewFile.value
    if (!canEdit.value || !file) return
    draft.value = file.content ?? ''
    error.value = ''
    conflicted.value = false
  }

  function cancel() {
    draft.value = null
    error.value = ''
    conflicted.value = false
  }

  /** 存下去。答 true 表示存住了；false 是没存成（原因在 `error`/`conflicted`）。 */
  async function save(): Promise<boolean> {
    const file = state.previewFile.value
    const tid = props.topicId
    if (!file || !tid || draft.value === null || saving.value) return false
    // 没改就别写一版：内容一样，白留一版历史。
    if (draft.value === file.content) {
      cancel()
      return true
    }
    saving.value = true
    error.value = ''
    conflicted.value = false
    try {
      await writeRoomTextFile(tid, file.path, draft.value, file.version)
      // 存下去了：立刻反映到预览台上，别让人以为没生效。正文和版本都换成刚存下的。
      file.content = draft.value
      draft.value = null
      refreshDocument()
      return true
    } catch (e) {
      // 别人在那之后存过：这一版不写，让读者选（刷新看最新的再改）。编辑里的字留着——
      // 读者多半想改一改再存，丢掉等于白打。
      if (e instanceof ApiError && e.status === 409) {
        conflicted.value = true
      } else {
        error.value = e instanceof Error ? e.message : t('work.room.preview.saveFileFailed')
      }
      return false
    } finally {
      saving.value = false
    }
  }

  function setDraft(value: string) {
    draft.value = value
  }

  return { draft, error, saving, conflicted, canEdit, begin, cancel, save, setDraft }
}
