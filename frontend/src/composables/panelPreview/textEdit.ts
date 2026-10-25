// 在预览格里直接改一份文本文件（markdown），存回那一份本身。
//
// 和概览文档那种多人实时协同不是一回事：那套走协作服务（Yjs），房间文件是磁盘上的
// 字节。这里做的是「能改、存得住、冲突不丢」——读的时候拿到一个内容版本，存的时候
// 带回去；文件在那之后被别人改过，后端答 409，读到的和别人的都不会被盖掉。
import type { ComputedRef, Ref } from 'vue'
import type { PreviewState } from './previewState'
import type { PanelPreviewProps } from './types'

import { computed, ref, watch } from 'vue'

import { ApiError, readPreviewFile } from '@/api'
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
  /** 这一次编辑钉住的是哪一份；没开着时是空串（用于标题）。 */
  pinnedPath: Ref<string>
  canEdit: ComputedRef<boolean>
  begin: () => void
  cancel: () => void
  save: () => Promise<boolean>
  /** 冲突之后读一遍最新的，把编辑器换成文件现在的样子（读者现在的改动会被换掉）。 */
  reload: () => Promise<void>
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
  /** 这一次编辑钉住的那一份：路径和「打开时读到的那一版」。
   *
   *  两个都必须在打开时就钉住，不能到存的时候再去问屏幕上摆的是哪一份：编辑器开着的
   *  这段时间里，面板会照旧重读这一份（轮询、收工、读者按了刷新），`previewFile`
   *  说不动就被换成别人的新版。照那时候的版本去存，服务端一比「对得上」，打进去的字
   *  就盖掉了别人的改动——正是这一条路要挡的事。钉住之后，别人先改过的还会是 409。 */
  const pinned = ref<{ path: string; version: string; content: string } | null>(null)
  const pinnedPath = computed(() => pinned.value?.path ?? '')

  /** 这一份能不能在预览里直接改：是文本、是这个房间的文件、正文读得出来。
   *
   *  判据挑得很明白：`content` 不是 null（二进制/太大的读不出正文也写不回去；空文件的
   * 正文是空串，也是「读得出」），不是资料库原件或消息附件（那两样不是这个房间的文件，
   * 写进去别人读到的会是同名的另一份——后端也拒），并且已经有不为空的版本（文件在，
   * 且读的时候它的版本拿得到）。 */
  const canEdit = computed(() => {
    const file = state.previewFile.value
    if (!file || !props.topicId) return false
    if (file.content === null) return false
    if (!file.version) return false
    if (file.binary || file.too_large) return false
    const path = file.path
    if (path.startsWith('library/') || path.startsWith('uploads/')) return false
    return state.documentType.value?.view === 'markdown'
  })

  function begin() {
    const file = state.previewFile.value
    if (!canEdit.value || !file || file.content === null || !file.version) return
    pinned.value = { path: file.path, version: file.version, content: file.content }
    draft.value = file.content
    error.value = ''
    conflicted.value = false
  }

  function cancel() {
    pinned.value = null
    draft.value = null
    error.value = ''
    conflicted.value = false
  }

  // 屏幕上那份文件换掉了（读者切到了别的文件、芝士换了当前预览）：编辑器里打的是
  // 上一份的字，再留在屏幕上就是「这一格写着文件 B，改的却是文件 A」。收起来。
  watch(
    () => state.previewFile.value?.path ?? '',
    (path) => {
      if (pinned.value && path !== pinned.value.path) cancel()
    }
  )

  /** 存下去。答 true 表示存住了；false 是没存成（原因在 `error`/`conflicted`）。 */
  async function save(): Promise<boolean> {
    const target = pinned.value
    const tid = props.topicId
    if (!target || !tid || draft.value === null || saving.value) return false
    // 没改就别写一版：内容一样，白留一版历史（比的是打开时那一版）。
    if (draft.value === target.content) {
      cancel()
      return true
    }
    saving.value = true
    error.value = ''
    conflicted.value = false
    try {
      const saved = await writeRoomTextFile(tid, target.path, draft.value, target.version)
      // 存下去了：立刻反映到预览台上，别让人以为没生效。正文和版本一起换成刚存下的
      // ——只换正文、留着旧版本，评论标记那几处会按一个早已过期的版本去比。
      const file = state.previewFile.value
      if (file && file.path === target.path) {
        file.content = draft.value
        if (saved.version) file.version = saved.version
      }
      cancel()
      refreshDocument()
      return true
    } catch (e) {
      // 别人在那之后存过：这一版不写，让读者先看看最新的。编辑里的字留着——
      // 读者可以先把它们拷走，再按「载入最新的」换成文件现在的样子。
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

  /** 冲突之后：读一遍文件现在的样子，把它换成编辑器里的正文，版本跟到最新。
   *
   *  这是一次明说的替换（提示条上就是这两个字），读者按下去就是认了现在这些改动不要了；
   *  不按就还留着，再存一次仍是 409，不会有人被悄悄盖掉。 */
  async function reload(): Promise<void> {
    const target = pinned.value
    const tid = props.topicId
    if (!target || !tid) return
    error.value = ''
    try {
      const latest = await readPreviewFile(tid, target.path)
      if (!pinned.value || pinned.value.path !== target.path) return
      if (latest.content === null || !latest.version) {
        error.value = t('work.room.preview.saveFileFailed')
        return
      }
      pinned.value = { path: target.path, version: latest.version, content: latest.content }
      draft.value = latest.content
      conflicted.value = false
      const file = state.previewFile.value
      if (file && file.path === target.path) {
        file.content = latest.content
        file.version = latest.version
      }
    } catch (e) {
      error.value = e instanceof Error ? e.message : t('work.room.preview.saveFileFailed')
    }
  }

  function setDraft(value: string) {
    draft.value = value
  }

  return { draft, error, saving, conflicted, pinnedPath, canEdit, begin, cancel, save, reload, setDraft }
}
