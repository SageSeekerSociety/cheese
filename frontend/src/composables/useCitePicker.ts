/** 芝士输入框里的 `@`：挑一个任务、频道、文档、资料库文件或题目，在光标处写下 `<#id>`，
 *  画出来是一枚标签。照原型（芝士的 @ 引用）：
 *
 * - 刚打 `@`：先是「最近去过」（和 Ctrl+K 同一份记录），下面是四类入口，点进去看这一类；
 *   不列当前页面，它已经在输入框上方的标签里了。
 * - 打了字：不用先选类，按类分组出结果，每组最多 4 条，眼前这个项目的排前面；名字和
 *   拼音首字母都能找（和 Ctrl+K 同一套 `match`）。题目不在候选里，打字时去搜。
 * - 一句最多引用 `MOST_CITED` 样；够了再打 `@` 只给一句说明。
 *
 * 这一层不取数：候选、最近去过、搜题目都由宿主给进来（`.claude/rules/architecture.md`）。
 */
import type { Ref } from 'vue'
import type { Citable, CitableKind } from '@/types/citable'

import { computed, ref, watch } from 'vue'

import { match } from '@/commands/palette/match'

/** 打到一半的 `@`：光标前面那一截的末尾。`<@handle>` 里的不算。 */
const PARTIAL_AT = /(?<!<)@([^\s@<>]*)$/
const TOKEN = /<#[\w-]+>/g
/** 四类入口的先后，也是打字时分组的先后（题目排最后，它是搜回来的）。 */
export const CITE_KINDS: CitableKind[] = ['task', 'channel', 'doc', 'file']
const GROUP_ORDER: CitableKind[] = [...CITE_KINDS, 'problem']
const PER_GROUP = 4
/** 一句最多引用这么多样（和后端 `citing.MOST` 一样）。 */
export const MOST_CITED = 5
const RECENT = 5

export type CiteRow =
  | { type: 'group'; label: 'recent' | 'kinds' | CitableKind }
  | { type: 'kind'; kind: CitableKind }
  | { type: 'item'; item: Citable; range: [number, number] | null }
  | { type: 'full' }

export interface CitePickerDeps {
  /** 输入框的正文和光标（按字符数）。 */
  draft: Ref<string>
  caret: Ref<number>
  /** 能引用的东西；还没读回来是 null。 */
  items: () => Citable[] | null
  /** 最近去过的 id，近的在前（只是一份顺序，不在候选里的不算）。 */
  recents: () => string[]
  /** 眼前这个项目叫什么；不在项目里是 null。 */
  project: () => string | null
  most: number
  /** 打开菜单：宿主去读候选。 */
  opened: () => void
  /** 挑中了一样：宿主记下它的名字（标签和这一句的引用照它写）。 */
  picked: (item: Citable) => void
  searchProblems?: (query: string) => Promise<Citable[]>
  scrollActiveIntoView: () => void
}

export function useCitePicker(deps: CitePickerDeps) {
  const before = computed(() => deps.draft.value.slice(0, deps.caret.value))
  const partial = computed(() => PARTIAL_AT.exec(before.value))
  /** Esc 收起的是哪一个 `@`（按它在正文里的位置）：光标离开它再回来才重新打开。 */
  const dismissed = ref<number | null>(null)
  const kind = ref<CitableKind | null>(null)
  const active = ref(0)
  const problems = ref<Citable[]>([])
  /** 这里挑中过的，按 id：输入框里的标签当场照它写名字，不等外面传回来。 */
  const picks = ref<Record<string, { kind: CitableKind; title: string }>>({})

  const open = computed(() => !!partial.value && partial.value.index !== dismissed.value)
  const query = computed(() => partial.value?.[1] ?? '')
  const cited = computed(() => (deps.draft.value.match(TOKEN) ?? []).length)

  watch(open, (now) => {
    if (now) deps.opened()
    else kind.value = null
  })
  watch(partial, (now) => {
    if (!now || now.index !== dismissed.value) dismissed.value = null
  })
  watch([query, kind], () => (active.value = 0))

  let asked = 0
  watch(query, async (q) => {
    problems.value = []
    if (!q.trim() || !deps.searchProblems || kind.value) return
    const mine = ++asked
    const found = await deps.searchProblems(q).catch(() => [])
    if (mine === asked) problems.value = found
  })

  const here = (item: Citable) => deps.project() !== null && item.where === deps.project()

  function matched(list: Citable[]): { item: Citable; range: [number, number] | null; score: number }[] {
    return list.flatMap((item) => {
      const hit = match(query.value, item.title, [item.where, item.place ?? ''])
      return hit ? [{ item, range: hit.range, score: hit.score + (here(item) ? 30 : 0) }] : []
    })
  }

  const rows = computed<CiteRow[]>(() => {
    if (cited.value >= deps.most) return [{ type: 'full' }]
    const items = deps.items() ?? []
    if (kind.value) {
      const ofKind = items.filter((i) => i.kind === kind.value && (kind.value === 'file' || !deps.project() || here(i)))
      return matched(ofKind)
        .sort((a, b) => b.score - a.score)
        .map(({ item, range }) => ({ type: 'item' as const, item, range }))
    }
    if (!query.value) {
      const byId = new Map(items.map((i) => [i.id, i]))
      const recent = deps
        .recents()
        .flatMap((id) => byId.get(id) ?? [])
        .slice(0, RECENT)
      return [
        ...(recent.length
          ? [
              { type: 'group' as const, label: 'recent' as const },
              ...recent.map((item) => ({ type: 'item' as const, item, range: null })),
            ]
          : []),
        { type: 'group' as const, label: 'kinds' as const },
        ...CITE_KINDS.map((k) => ({ type: 'kind' as const, kind: k })),
      ]
    }
    const found = matched([...items, ...problems.value])
    return GROUP_ORDER.flatMap((k) => {
      const group = found
        .filter((f) => f.item.kind === k)
        .sort((a, b) => b.score - a.score)
        .slice(0, PER_GROUP)
      return group.length
        ? [
            { type: 'group' as const, label: k },
            ...group.map(({ item, range }) => ({ type: 'item' as const, item, range })),
          ]
        : []
    })
  })

  /** 能挑的几行（分组标题不算），键盘和鼠标的高亮按它数。 */
  const choices = computed(() => rows.value.filter((r) => r.type === 'item' || r.type === 'kind'))

  function choose(row: CiteRow) {
    if (row.type === 'kind') {
      kind.value = row.kind
      return
    }
    if (row.type !== 'item' || !partial.value) return
    const start = partial.value.index
    const rest = deps.draft.value.slice(deps.caret.value)
    // 标签后面空一格好接着打字；后面本来就是空白就不再添。
    const token = `<#${row.item.id}>${/^\s/.test(rest) ? '' : ' '}`
    picks.value = { ...picks.value, [row.item.id]: { kind: row.item.kind, title: row.item.title } }
    deps.picked(row.item)
    deps.draft.value = deps.draft.value.slice(0, start) + token + rest
    deps.caret.value = start + token.length
  }

  function back() {
    kind.value = null
  }

  /** 输入框底下那颗 @：在光标处打一个 `@`，菜单随之打开。 */
  function start() {
    deps.draft.value = deps.draft.value.slice(0, deps.caret.value) + '@' + deps.draft.value.slice(deps.caret.value)
    deps.caret.value += 1
  }

  /** 鼠标指着第几行能挑的：高亮跟过去，和键盘改的是同一个值。 */
  function point(index: number) {
    active.value = index
  }

  function close() {
    if (partial.value) dismissed.value = partial.value.index
  }

  /** 输入框的按键先过这里；认领了返回 true（外面不再当发送、换行处理）。 */
  function onKeydown(e: KeyboardEvent): boolean {
    if (!open.value || e.isComposing) return false
    const n = choices.value.length
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      if (!n) return false
      e.preventDefault()
      active.value = (active.value + (e.key === 'ArrowDown' ? 1 : n - 1)) % n
      deps.scrollActiveIntoView()
      return true
    }
    if ((e.key === 'Enter' || e.key === 'Tab') && !e.shiftKey && n) {
      e.preventDefault()
      choose(choices.value[active.value])
      return true
    }
    if (e.key === 'Escape') {
      e.preventDefault()
      close()
      return true
    }
    if (e.key === 'Backspace' && kind.value && !query.value) {
      e.preventDefault()
      back()
      return true
    }
    return false
  }

  return {
    open,
    query,
    kind,
    rows,
    choices,
    active,
    loading: computed(() => deps.items() === null),
    choose,
    back,
    point,
    start,
    picks,
    close,
    onKeydown,
  }
}

export type CitePicker = ReturnType<typeof useCitePicker>
