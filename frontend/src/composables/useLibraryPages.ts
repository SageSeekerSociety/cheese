// 资料库页那一张表：资料库（文件夹、文件）和文档两串各自一页一页地取，按 rank 并起来。
//
// 不搜不筛时看的是一层（`dir`）：资料库给这一层的文件夹和文件；最上层再加上文档，
// 和文件按改动时间混排（文档没有文件夹）。按名字搜或按类型筛时是整个资料库平铺；
// 只有筛「文档」类且没在搜的时候文档也在里面（搜的时候文档按正文找，结果另列）。
//
// 一次只摆出两串都已经取到的那一段（`lib/rankMerge.ts`），所以往下滚动时取下一页，
// 顺序和一次全排好一样。条件一变就从头取。
import type { ListedDocument } from '../api/projectDocuments'
import type { LibraryEntry, LibraryFile, LibraryFolder, LibraryShelf } from '../lib/libraryApi'
import type { Kind } from '../lib/libraryKinds'

import { computed, nextTick, type Ref, ref, watch } from 'vue'
import { useEventListener } from '@vueuse/core'

import { listProjectDocuments } from '../api/projectDocuments'
import { listTeamDocuments } from '../api/teamDocuments'
import { listProjectLibrary } from '../lib/libraryApi'
import { type MergeSource, starving, takeReady } from '../lib/rankMerge'

/** 一次往下摆这么多行。 */
const PAGE = 50
/** 离底还有这么远（px）就去取下一页。 */
const NEAR_BOTTOM = 200

export type LibraryRow = { key: string; rank: string } & (
  | { folder: LibraryFolder; file?: never; doc?: never }
  | { file: LibraryFile; folder?: never; doc?: never }
  | { doc: ListedDocument; folder?: never; file?: never }
)

export interface LibraryView {
  dir: string
  q: string
  kind: Kind
}

type Item = LibraryEntry | (ListedDocument & { type: 'doc' })

interface Source extends MergeSource<Item> {
  next: string | null
  fetch(cursor: string | null): Promise<{ rows: Item[]; next: string | null }>
}

function toRow(item: Item): LibraryRow {
  if (item.type === 'folder') return { key: `dir:${item.path}`, rank: item.rank, folder: item }
  if (item.type === 'file') return { key: `file:${item.path}`, rank: item.rank, file: item }
  return { key: `doc:${item.id}`, rank: item.rank, doc: item }
}

/** `scroller` 是这张表自己的滚动容器：滚到离底不远、或者一页没把它填满，就取下一页。
 *  团队的资料库（`shelf` 不是项目编号）里是团队自己的文档：它们一次全给，没有按正文
 *  找的接口，搜的时候按标题筛。 */
export function useLibraryPages(
  shelf: () => LibraryShelf,
  view: () => LibraryView,
  scroller: Readonly<Ref<HTMLElement | null>>,
  active: () => boolean = () => true
) {
  const rows = ref<LibraryRow[]>([])
  const loading = ref(false)
  const loadFailed = ref(false)
  const loadReason = ref('')
  let sources: Source[] = []
  let generation = 0

  function build(pid: LibraryShelf, { dir, q, kind }: LibraryView): Source[] {
    const query = q.trim()
    const flat = !!query || kind !== 'all'
    const library: Source = {
      buffer: [],
      done: false,
      next: null,
      fetch: async (cursor) => {
        const page = await listProjectLibrary(pid, {
          ...(flat ? { flat: true, q: query, kind: kind === 'all' ? undefined : kind } : { dir }),
          cursor,
          limit: PAGE,
        })
        return { rows: page.data, next: page.next }
      },
    }
    if (typeof pid !== 'string') {
      const team = pid.team
      const withTeamDocs = (kind === 'doc' || kind === 'all') && (flat || !dir)
      if (!withTeamDocs) return [library]
      const needle = query.toLocaleLowerCase()
      const teamDocs: Source = {
        buffer: [],
        done: false,
        next: null,
        fetch: async () => {
          const page = await listTeamDocuments(team)
          const rows = page.data.filter((doc) => !needle || (doc.title ?? '').toLocaleLowerCase().includes(needle))
          return { rows: rows.map((doc) => ({ ...doc, type: 'doc' as const })), next: null }
        },
      }
      return [library, teamDocs]
    }
    const withDocs = !query && (kind === 'doc' || (kind === 'all' && !dir))
    if (!withDocs) return [library]
    const documents: Source = {
      buffer: [],
      done: false,
      next: null,
      fetch: async (cursor) => {
        const page = await listProjectDocuments(pid, cursor, PAGE)
        return { rows: page.data.map((doc) => ({ ...doc, type: 'doc' as const })), next: page.next }
      },
    }
    return [library, documents]
  }

  /** 往下再摆一页：取空了的那几串的下一页，把能摆的摆出来。 */
  async function more() {
    if (loading.value || exhausted.value) return
    const mine = generation
    loading.value = true
    loadFailed.value = false
    try {
      let added = 0
      while (added < PAGE) {
        const hungry = starving(sources)
        await Promise.all(
          hungry.map(async (i) => {
            const source = sources[i]
            const page = await source.fetch(source.next)
            if (mine !== generation) return
            source.buffer.push(...page.rows)
            source.next = page.next
            source.done = !page.next
          })
        )
        if (mine !== generation) return
        const ready = takeReady(sources)
        rows.value = [...rows.value, ...ready.map(toRow)]
        added += ready.length
        if (!ready.length && !starving(sources).length) break
      }
    } catch (e) {
      if (mine !== generation) return
      loadFailed.value = true
      loadReason.value = e instanceof Error ? e.message : ''
    } finally {
      if (mine === generation) loading.value = false
    }
    if (mine === generation && !loadFailed.value) void nextTick(fillIfShort)
  }

  /** 滚到离底不远了（或者根本没填满）：接着取。 */
  function fillIfShort() {
    const el = scroller.value
    if (!el || loading.value || exhausted.value || loadFailed.value) return
    if (el.scrollHeight - el.scrollTop - el.clientHeight <= NEAR_BOTTOM) void more()
  }
  useEventListener(scroller, 'scroll', fillIfShort, { passive: true })

  /** 从头取：换了一层、换了搜索或筛选、或者清单变了（放进来、挪走、删掉之后）。 */
  function reset() {
    generation++
    sources = build(shelf(), view())
    rows.value = []
    loading.value = false
    loadFailed.value = false
    loadReason.value = ''
    if (active()) void more()
  }

  /** 摆完了：每一串都取完了，手里也都空了。 */
  const exhausted = computed(() => {
    void rows.value
    return sources.length > 0 && sources.every((source) => source.done && !source.buffer.length)
  })

  watch(
    // 团队的书架每次是一个新对象：按它是哪一个来比，不按是不是同一个对象。
    () => [JSON.stringify(shelf()), view().dir, view().q.trim(), view().kind] as const,
    () => reset(),
    { immediate: true }
  )

  watch(active, (isActive) => {
    if (!isActive) {
      generation++
      loading.value = false
      return
    }
    if (!rows.value.length && !loading.value && !exhausted.value && !loadFailed.value) void more()
    else void nextTick(fillIfShort)
  })

  /** 一份文档在别处改了名：表里那一行跟着改，不必从头取。 */
  function renameDoc(id: string, title: string) {
    rows.value = rows.value.map(
      (row): LibraryRow => (row.doc?.id === id ? { key: row.key, rank: row.rank, doc: { ...row.doc, title } } : row)
    )
  }

  /** 这几行不在了（删掉了）：从表里拿掉，不必从头取。 */
  function drop(gone: (row: LibraryRow) => boolean) {
    rows.value = rows.value.filter((row) => !gone(row))
  }

  return { rows, loading, loadFailed, loadReason, exhausted, more, reset, renameDoc, drop }
}
