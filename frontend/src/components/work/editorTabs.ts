// 编辑器那一种摆法里中间那一排和下面那一排：各开着哪几个页签、翻到哪一个。按任务记在这台设备上，
// 和「对话」那一种摆法的 `?tab=` 是两份：进编辑器时回到上一次在编辑器里的样子，不因为对话那一种
// 开着概览就把概览摆到中间。
//
// 中间那一排：打开的文件在最前（它们是哪几份、翻到哪一份是任务文件那一份记着的，`useTaskFiles`），
// 这里把它们合起来叫 `FILE_TABS`；后面按打开的先后是别的：概览、预览、改动（全部改动 `changes`，
// 或一份文件的改动 `diff:<路径>`），和从对话里打开的一份产出（`file:<路径>`）。下面那一排翻到的是
// 一个终端（`terminal:<id>`）或「现场」（`site`）。
//
// 刷新之后回来的只有不靠这一次会话里别的东西的那几样（概览、预览、改动）；从对话里打开的产出
// 跟着这一次会话里打开的那几份走。
import { ref, watch } from 'vue'

import { storageGetJson, storageRemove, storageSetJson } from '@/lib/browserStorage'

/** 中间那一排里打开的文件那几个页签合起来：翻到它就是翻到打开着的那一份文件。 */
export const FILE_TABS = 'code'
const DIFF = 'diff:'
/** 一份文件的改动那个页签。 */
export const diffKey = (path: string): string => DIFF + path
export const diffPath = (key: string): string | null => (key.startsWith(DIFF) ? key.slice(DIFF.length) : null)
/** 中间那一排里的一个页签显示的是工作面板的哪一格：全部改动和一份文件的改动都是「改动」那一格。 */
export function paneOfCentre(key: string): string {
  return key.startsWith(DIFF) ? 'changes' : key
}

const KEY = 'cheesex.editorTabs.'
const kept = (key: string) => key === 'overview' || key === 'preview' || key === 'changes' || key.startsWith(DIFF)

interface Saved {
  extra: string[]
  centre: string
  bottom: string
}

export function useEditorTabs(taskId: () => string | null | undefined) {
  /** 中间那一排里打开的文件以外的页签，按打开的先后。 */
  const extra = ref<string[]>([])
  /** 中间那一排翻到的那一个：`FILE_TABS` 或 `extra` 里的一个。 */
  const centre = ref<string>(FILE_TABS)
  /** 下面那一排翻到的那一个。 */
  const bottom = ref<string>('site')

  watch(
    taskId,
    (id) => {
      const saved = id ? storageGetJson<Partial<Saved>>(KEY + id) : null
      extra.value = Array.isArray(saved?.extra) ? saved.extra.filter((key) => typeof key === 'string' && kept(key)) : []
      const front = saved?.centre
      centre.value = typeof front === 'string' && extra.value.includes(front) ? front : FILE_TABS
      bottom.value = typeof saved?.bottom === 'string' ? saved.bottom : 'site'
    },
    { immediate: true }
  )

  function save() {
    const id = taskId()
    if (!id) return
    const keep = extra.value.filter(kept)
    const front = keep.includes(centre.value) ? centre.value : FILE_TABS
    if (!keep.length && front === FILE_TABS && bottom.value === 'site') storageRemove(KEY + id)
    else storageSetJson(KEY + id, { extra: keep, centre: front, bottom: bottom.value } satisfies Saved)
  }

  /** 在中间那一排打开一个页签（开着的就翻到它）。 */
  function open(key: string) {
    if (key !== FILE_TABS && !extra.value.includes(key)) extra.value = [...extra.value, key]
    centre.value = key
    save()
  }
  /** 关掉中间那一排里的一个：翻着它时翻到它右边那一个，没有就左边那一个，再没有就是打开的文件。 */
  function close(key: string) {
    const at = extra.value.indexOf(key)
    if (at < 0) return
    const rest = extra.value.filter((one) => one !== key)
    extra.value = rest
    if (centre.value === key) centre.value = rest[at] ?? rest[at - 1] ?? FILE_TABS
    save()
  }
  /** 换掉中间那一排里的一个（同一个位置），翻着它时翻到新的那一个。 */
  function replace(key: string, next: string) {
    const at = extra.value.indexOf(key)
    if (at < 0) return open(next)
    extra.value = extra.value.map((one) => (one === key ? next : one)).filter((one, i, all) => all.indexOf(one) === i)
    if (centre.value === key) centre.value = next
    save()
  }
  /** 下面那一排翻到一个。 */
  function showBottom(key: string) {
    bottom.value = key
    save()
  }

  return { extra, centre, bottom, open, close, replace, showBottom }
}
