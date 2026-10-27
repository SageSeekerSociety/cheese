// 「预览」这一格顶上的版本：这一份是第几版、谁存的、什么时候；这一版比上一版改了几处；
// 两版之间改了什么；换到某一版看看。
//
// 版本清单就是「保存历史」那一份（`useRoomFileHistory`，编辑器旁边那一栏读的也是它），
// 这里不另取一遍：两处对「这是第几版」只能有一个答案。屏幕上这一份是哪一版，按内容哈希
// 对：清单里 `version` 和这一份的 `version` 相同的那一行。对不上（文件被清单之外的路改过）
// 就不说「第几版」——说错比不说更糟。
//
// 两版改了什么在前端比：历史接口给得出每一版的原始字节。三种文件比得出来：
//   - Markdown：原文逐段比（`lib/versionDiff`）。
//   - Word（.docx）：取出正文逐段比（`lib/docxText`）；排版的改动看不出来。
//   - 表格：两版读成同样的表逐格比（`lib/sheetDiff`）。
// PDF、演示稿这些不比：它们的字在版面里，逐段对不上号，标出来的「改动」多半是错的。
//
// 资料库里的文件没有这一份历史（只读的原件），所以那上面什么都不摆。
import type { Ref } from 'vue'
import type { RoomFileRevision } from '../../api'
import type { SheetDiff } from '../../lib/sheetDiff'
import type { VersionDiff } from '../../lib/versionDiff'
import type { DocPeopleBundle } from '../useDocPeople'
import type { RoomFileHistoryBundle } from '../useRoomFileHistory'
import type { PreviewState } from './previewState'
import type { PanelPreviewProps } from './types'

import { computed, ref, shallowRef, watch } from 'vue'

import { roomFileRevisionBytes } from '../../api'
import { agentNames } from '../../lib/agentNames'
import { storageGet, storageSet } from '../../lib/browserStorage'
import { docxParagraphs } from '../../lib/docxText'
import { isLibraryPath } from '../../lib/library'
import { diffSheets } from '../../lib/sheetDiff'
import { readSheets } from '../../lib/sheetRead'
import { BLANK_LINE, changeRuns, versionDiff } from '../../lib/versionDiff'

import { t } from '@/i18n'

/** 版本清单里的一行，画的那一层要的样子。 */
export interface VersionEntry {
  id: string
  seq: number
  /** 谁存的这一版：名册上的名字；队友存的、名册里没有就用队友的名字。 */
  author: string
  at: string
  note: string | null
  /** 屏幕上这一份就是这一版。 */
  current: boolean
  /** 清单里最新的一版。 */
  latest: boolean
  /** 读者上次看到的就是这一版（比当前旧时才标：「从这一版之后改了什么」）。 */
  lastSeen: boolean
}

/** 两版之间改了什么。`text` 是逐段的（Markdown、Word），`sheet` 是逐格的。 */
export type VersionChanges =
  | { kind: 'text'; markdown: boolean; diff: VersionDiff; count: number }
  | { kind: 'sheet'; diff: SheetDiff; count: number }

/** 正在比的两版，和比出来的结果（还在读时是 null）。 */
export interface VersionCompare {
  base: number
  target: number
  changes: VersionChanges | null
  loading: boolean
  error: string
}

/** 正在看的旧版：画得出来的那几种（Markdown 原文、表格、PDF）。 */
export type OldVersionView =
  | { seq: number; kind: 'markdown'; text: string | null }
  | { seq: number; kind: 'sheet' | 'pdf'; bytes: ArrayBuffer | null }

type DiffKind = 'markdown' | 'docx' | 'sheet'

export interface VersionsDeps {
  props: PanelPreviewProps
  state: PreviewState
  fileHistory: RoomFileHistoryBundle
  people: DocPeopleBundle
  /** 现在开着编辑会话：版本那一套先收起来（编辑器自己有一栏历史）。 */
  editing: Ref<string | null>
}

// 每一版的字节是不变的（按版本 id 存），读过就留着。只留最近几份：一份工作簿可能有几 MB。
const BYTES_KEEP = 6
const bytesCache = new Map<string, Promise<ArrayBuffer>>()

function revisionBytes(topicId: string, id: string): Promise<ArrayBuffer> {
  const hit = bytesCache.get(id)
  if (hit) {
    bytesCache.delete(id)
    bytesCache.set(id, hit)
    return hit
  }
  const read = roomFileRevisionBytes(topicId, id)
  bytesCache.set(id, read)
  read.catch(() => bytesCache.delete(id))
  while (bytesCache.size > BYTES_KEEP) bytesCache.delete(bytesCache.keys().next().value as string)
  return read
}

/** Markdown 的空行换成占位字符再比（见 `lib/versionDiff` 的 `BLANK_LINE`）。 */
const keepBlankLines = (text: string) =>
  text
    .split('\n')
    .map((line) => (line.trim() ? line : BLANK_LINE))
    .join('\n')

const decodeUtf8 = (bytes: ArrayBuffer) => new TextDecoder().decode(bytes)

/** 「看到第几版了」按房间 + 路径记在这台浏览器里：提示条收起来之后，同一版不再提示。 */
const seenKey = (topicId: string, path: string) => `cheese.preview.seenVersion:${topicId}:${path}`

export function usePanelPreviewVersions(deps: VersionsDeps) {
  const { props, state, fileHistory, people, editing } = deps

  const path = computed(() => state.previewFile.value?.path ?? null)
  const roomFile = computed(() => !!path.value && !isLibraryPath(path.value) && !editing.value)

  const rows = computed<RoomFileRevision[]>(() => {
    if (!roomFile.value) return []
    // 清单读的可能还是上一份文件（换文件时先清空再重读）：路径对不上的不算。
    return fileHistory.rows.value.filter((row) => row.path === path.value)
  })

  const currentRow = computed(() => {
    const version = state.previewFile.value?.version
    if (!version) return null
    return rows.value.find((row) => row.version === version) ?? null
  })

  // 队友也在项目名册上（`useDocPeople` 只收了人和这间房的那一位）：存下这一版的可能是
  // 别的队友，名字照名册写，不笼统写成「芝士」。
  const agents = computed(() => agentNames([], props.members ?? []))
  function authorOf(row: RoomFileRevision): string {
    const named = row.author ? people.names.value[row.author] ?? agents.value.get(row.author) : undefined
    if (named) return named
    if (row.author_kind === 'agent') return props.agentName || t('work.room.defaultAgentName')
    return row.author || t('work.room.versions.someone')
  }

  // ---- 「看到第几版了」 ----
  const seenTick = ref(0)
  const seen = computed(() => {
    void seenTick.value
    const tid = props.topicId
    if (!tid || !path.value) return null
    const raw = storageGet(seenKey(tid, path.value))
    const n = raw === null ? NaN : Number(raw)
    return Number.isFinite(n) ? n : null
  })
  function markSeen() {
    const tid = props.topicId
    const row = currentRow.value
    if (!tid || !path.value || !row) return
    storageSet(seenKey(tid, path.value), String(row.seq))
    seenTick.value += 1
  }

  const list = computed<VersionEntry[]>(() =>
    rows.value.map((row, index) => ({
      id: row.id,
      seq: row.seq,
      author: authorOf(row),
      at: row.created_at,
      note: row.note,
      current: row.id === currentRow.value?.id,
      latest: index === 0,
      lastSeen: seen.value === row.seq && row.seq < (currentRow.value?.seq ?? 0),
    }))
  )
  const current = computed(() => list.value.find((entry) => entry.current) ?? null)

  const diffKind = computed<DiffKind | null>(() => {
    const type = state.documentType.value
    if (!type) return null
    if (type.view === 'markdown') return 'markdown'
    if (state.documentSuffix.value === 'docx') return 'docx'
    if (type.view === 'sheet' && type.sheet !== 'ods') return 'sheet'
    return null
  })
  /** 旧版能不能直接画出来：不用服务器转换的那几种才行（转换只认文件的当前内容）。 */
  const viewKind = computed<OldVersionView['kind'] | null>(() => {
    const type = state.documentType.value
    if (!type) return null
    if (type.view === 'markdown') return 'markdown'
    if (type.view === 'sheet' && type.sheet !== 'ods') return 'sheet'
    if (state.documentSuffix.value === 'pdf') return 'pdf'
    return null
  })

  const older = (seq: number) => rows.value.find((row) => row.seq < seq) ?? null
  const bySeq = (seq: number) => rows.value.find((row) => row.seq === seq) ?? null
  const canCompare = computed(() => !!diffKind.value && !!currentRow.value && !!older(currentRow.value.seq))

  /** 提示条拿哪一版比：读者上次看到的那一版（还在清单里的话），没记过就是上一版。 */
  const hintBase = computed(() => {
    const row = currentRow.value
    if (!row || !canCompare.value) return null
    const last = seen.value
    if (last !== null && last >= row.seq) return null
    const base = (last !== null ? bySeq(last) : null) ?? older(row.seq)
    return base && base.seq < row.seq ? base : null
  })

  // ---- 比两版 ----
  async function changesBetween(base: RoomFileRevision, target: RoomFileRevision): Promise<VersionChanges | null> {
    const tid = props.topicId
    const kind = diffKind.value
    if (!tid || !kind) return null
    const [before, after] = await Promise.all([revisionBytes(tid, base.id), revisionBytes(tid, target.id)])
    if (kind === 'sheet') {
      const sheetKind = state.documentType.value?.sheet ?? 'workbook'
      const [a, b] = await Promise.all([readSheets(before, sheetKind), readSheets(after, sheetKind)])
      if (a.failure || b.failure) throw new Error(a.failure || b.failure)
      const diff = diffSheets(a.sheets, b.sheets)
      return { kind: 'sheet', diff, count: diff.cells.length + diff.addedSheets.length + diff.removedSheets.length }
    }
    let oldText: string
    let newText: string
    if (kind === 'docx') {
      const [a, b] = await Promise.all([docxParagraphs(before), docxParagraphs(after)])
      if (!a || !b) throw new Error(t('work.room.versions.unreadable'))
      oldText = a.join('\n')
      newText = b.join('\n')
    } else {
      oldText = decodeUtf8(before)
      newText = decodeUtf8(after)
    }
    if (kind === 'markdown') {
      oldText = keepBlankLines(oldText)
      newText = keepBlankLines(newText)
    }
    const diff = versionDiff(oldText, newText, { collapse: false })
    return { kind: 'text', markdown: kind === 'markdown', diff, count: changeRuns(diff.rows) }
  }

  /** 一对版本的比较结果，按「哪两版」记：提示条和对比态比的常常是同一对，不读两遍。 */
  function useComparison(pair: () => { base: RoomFileRevision; target: RoomFileRevision } | null) {
    const changes = shallowRef<VersionChanges | null>(null)
    const loading = ref(false)
    const error = ref('')
    let key = ''
    watch(
      () => {
        const p = pair()
        return p ? `${p.base.id}>${p.target.id}:${diffKind.value}` : ''
      },
      async (next) => {
        key = next
        changes.value = null
        error.value = ''
        const p = pair()
        if (!next || !p) {
          loading.value = false
          return
        }
        loading.value = true
        try {
          const made = await changesBetween(p.base, p.target)
          if (key === next) changes.value = made
        } catch (e) {
          if (key === next) error.value = e instanceof Error ? e.message : t('work.room.versions.compareFailed')
        } finally {
          if (key === next) loading.value = false
        }
      },
      { immediate: true }
    )
    return { changes, loading, error }
  }

  const hintPair = computed(() =>
    hintBase.value && currentRow.value ? { base: hintBase.value, target: currentRow.value } : null
  )
  const hintRead = useComparison(() => hintPair.value)
  /** 「本版共修改 N 处」：比出来是 0 处（只改了排版、或者存了一份一样的）就不提示。 */
  const hint = computed(() => {
    const pair = hintPair.value
    const changes = hintRead.changes.value
    if (!pair || !changes || !changes.count) return null
    return { base: pair.base.seq, count: changes.count, unit: changes.kind === 'sheet' ? 'cell' : 'place' } as const
  })

  // ---- 对比态 / 看旧版 ----
  const compareBase = ref<number | null>(null)
  const oldSeq = ref<number | null>(null)

  const comparePair = computed(() => {
    const target = currentRow.value
    const base = compareBase.value === null ? null : bySeq(compareBase.value)
    return target && base && base.seq < target.seq ? { base, target } : null
  })
  const compareRead = useComparison(() => comparePair.value)
  const compare = computed<VersionCompare | null>(() => {
    const pair = comparePair.value
    if (!pair) return null
    return {
      base: pair.base.seq,
      target: pair.target.seq,
      changes: compareRead.changes.value,
      loading: compareRead.loading.value,
      error: compareRead.error.value,
    }
  })

  const oldBytes = shallowRef<ArrayBuffer | null>(null)
  const oldError = ref('')
  watch(
    () => (oldSeq.value === null ? null : bySeq(oldSeq.value)?.id ?? null),
    async (id) => {
      oldBytes.value = null
      oldError.value = ''
      const tid = props.topicId
      if (!id || !tid) return
      try {
        const bytes = await revisionBytes(tid, id)
        if (bySeq(oldSeq.value ?? -1)?.id === id) oldBytes.value = bytes
      } catch (e) {
        oldError.value = e instanceof Error ? e.message : t('work.room.versions.loadFailed')
      }
    }
  )
  const oldView = computed<OldVersionView | null>(() => {
    const seq = oldSeq.value
    const kind = viewKind.value
    if (seq === null || !kind || !bySeq(seq)) return null
    if (kind === 'markdown') return { seq, kind, text: oldBytes.value ? decodeUtf8(oldBytes.value) : null }
    return { seq, kind, bytes: oldBytes.value }
  })

  /** 进对比态：拿 `base` 那一版和屏幕上这一版比；不给就用提示条那一版，再没有就用上一版。 */
  function openCompare(base?: number) {
    const row = currentRow.value
    if (!row || !canCompare.value) return
    compareBase.value = base ?? hintBase.value?.seq ?? older(row.seq)?.seq ?? null
    oldSeq.value = null
    markSeen()
  }
  function exitCompare() {
    compareBase.value = null
  }
  function dismissHint() {
    markSeen()
  }
  /** 在版本菜单里点了一版：当前那一版就回到正文；旧版画得出来就看它，画不出来就拿它比。 */
  function choose(seq: number) {
    if (seq === currentRow.value?.seq) {
      oldSeq.value = null
      compareBase.value = null
      return
    }
    if (compareBase.value !== null && canCompare.value) {
      openCompare(seq)
      return
    }
    if (viewKind.value) {
      oldSeq.value = seq
      compareBase.value = null
      return
    }
    if (canCompare.value) openCompare(seq)
  }
  function backToLatest() {
    oldSeq.value = null
    compareBase.value = null
  }

  // 换了文件、或者文件出了新版：对比和旧版都是对上一份说的，收起来。
  watch(
    () => [props.topicId, path.value, currentRow.value?.id ?? null, editing.value],
    () => backToLatest()
  )

  /** 表格上改过的那几格（「表名!地址」→ 上一版写的什么）：对比态里画，提示条还在时也画——
   *  读者一眼看见芝士动了哪几格，不用先点「查看改动」。 */
  const changedCells = computed<Map<string, string> | null>(() => {
    const changes = compare.value ? compare.value.changes : hint.value ? hintRead.changes.value : null
    if (!changes || changes.kind !== 'sheet') return null
    return new Map(changes.diff.cells.map((cell) => [`${cell.sheet}!${cell.address}`, cell.before]))
  })
  /** 改了那几格的是谁（比较里新的那一版的作者）。 */
  const changedBy = computed(() => {
    const seq = compare.value?.target ?? currentRow.value?.seq
    return list.value.find((entry) => entry.seq === seq)?.author ?? ''
  })

  return {
    list,
    changedCells,
    changedBy,
    current,
    canCompare,
    /** 菜单里点一版会发生什么：能直接看旧版、只能比、还是什么都做不了。 */
    canView: computed(() => !!viewKind.value),
    hint,
    compare,
    oldView,
    oldError,
    openCompare,
    exitCompare,
    dismissHint,
    choose,
    backToLatest,
  }
}

/** 版本这一包原样递给面板（props）：面板自己不认识接口。 */
export type PreviewVersionsBundle = ReturnType<typeof usePanelPreviewVersions>
