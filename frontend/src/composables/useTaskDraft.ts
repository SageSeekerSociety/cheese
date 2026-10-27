// 发布页、改题页上的那份草稿（`@/types/taskDrafts`）：表单和服务端那份怎么保持一致。
//
// 两只手往里写：
//
// - **人**在页面上改：每改一格就存（停手 600ms 后一起存），存的是改了的那几格。还没有
//   草稿（发布页、改题页刚打开）时，第一次改动或第一次问芝士才建一份：发布页的记进地址栏，
//   刷新能接着写；改题页的是这道题的那份修改。
// - **芝士**答题时写：它的参数边写边流过来，页面先把能确定的几格填上（预览，不存——
//   这几格这时归芝士，人的保存跳过它们）；调用真正写进草稿以后，服务端推回来的改动才是
//   定数，覆盖预览、记成已存；调用报错就把预览收回。
//
// 「已存」那一份（`saved`）是比较的基准：表单报上来的值和它不一样的格子，才是人改的。
import type { AssistantEdit } from '@/stores/agentThread'
import type { TaskDraft, TaskDraftField, TaskDraftFields } from '@/types'

import { computed, nextTick, ref } from 'vue'

import { TaskDraftsApi } from '@/api/taskDrafts'
import { AGENT_FIELDS, type DraftTerms, type Preview, previewOf } from '@/lib/taskDraftAgent'

/** 表单那一头：读现在的样子，填进几格。 */
export interface DraftForm {
  values: () => TaskDraftFields
  apply: (fields: TaskDraftFields) => Promise<TaskDraftFields>
}

export type SaveState = 'idle' | 'saving' | 'saved' | 'failed'

const SAVE_AFTER_MS = 600

function same(a: unknown, b: unknown): boolean {
  return JSON.stringify(a ?? null) === JSON.stringify(b ?? null)
}

export interface DraftOptions {
  /** 新建草稿时它在哪个空间。 */
  spaceId: () => number | null
  /** 改的是这道已发布的题目：建的是它的那份修改，不是空间里一道新题目。 */
  taskId?: () => number | null
  /** 建好了一份草稿：页面把编号写进地址栏。 */
  started?: (draft: TaskDraft) => void
}

export function useTaskDraft(options: DraftOptions) {
  const draft = ref<TaskDraft | null>(null)
  const saveState = ref<SaveState>('idle')
  /** 服务端那一份（人存下的和芝士写进的），比较的基准。 */
  let saved: TaskDraftFields = {}
  /** 还没存的人的改动。 */
  let pending: TaskDraftFields = {}
  let timer: ReturnType<typeof setTimeout> | null = null
  /** 建草稿、存改动排成的一条队：一件做完才做下一件，后一件看得到前一件的结果。谁先要建
   *  （人改了、问芝士、离开页面）都排在这里，人的改动都在 `pending` 里等它。 */
  let queue: Promise<void> = Promise.resolve()
  /** 队里还没做完的件数。 */
  let inFlight = 0
  /** 上一次存没成：改动还在 `pending` 里，等下一次。 */
  let failed = false
  /** 换了一份草稿（`open`）就加一：队里排着的旧事做完，也不再动这一份。 */
  let epoch = 0
  let form: DraftForm | null = null
  /** 芝士正在预览的格子：人的保存先不碰它们。 */
  const previewing = ref(new Set<TaskDraftField>())
  /** 芝士正在写的那一格（它的字段名），指针停在这里。 */
  const writing = ref<string | null>(null)
  /** 芝士正在改的详情里那一段（见 `previewOf`）：指针先选中原文，再跟着新文字走。 */
  const passage = ref<NonNullable<Preview['passage']> | null>(null)
  /** 芝士停在一格前面、还没开始写（`lib/typist.ts` 的 held）：原来有字就先选中一下。 */
  const holding = ref(false)
  /** 芝士写过、人还没再碰的格子（芝士的字段名），各自是哪次写入。 */
  const marks = ref<Record<string, number>>({})
  /** 已经发布了：这份草稿没了，页面不再存。 */
  const published = ref(false)

  const id = computed(() => draft.value?.id ?? null)
  /** 正在把值填进表单：这期间表单报上来的变化是我们自己填的，不是人改的。 */
  let applying = 0

  async function put(fields: TaskDraftFields): Promise<TaskDraftFields> {
    if (!form) return {}
    applying += 1
    try {
      const shown = await form.apply(fields)
      await nextTick()
      return shown
    } finally {
      applying -= 1
    }
  }

  function bind(handle: DraftForm | null) {
    form = handle
  }

  /** 存到哪了：人有改动没存、或队里还有事，就是还在存；都存下了才是已保存。 */
  function refresh() {
    if (failed) saveState.value = 'failed'
    else if (inFlight || Object.keys(pending).length) saveState.value = 'saving'
    else saveState.value = draft.value ? 'saved' : 'idle'
  }

  /** 打开一份草稿（或者还没有草稿：从空白开始）。 */
  function open(row: TaskDraft | null, fields: TaskDraftFields = {}) {
    epoch += 1
    if (timer) clearTimeout(timer)
    timer = null
    draft.value = row
    saved = { ...(row?.fields ?? fields) }
    pending = {}
    failed = false
    marks.value = {}
    previewing.value = new Set()
    writing.value = null
    published.value = false
    refresh()
  }

  /** 页面刚把这些装进表单（表单刚挂上、附件刚读回来）：它们就是起点，不算人改的。 */
  function baseline(fields: TaskDraftFields) {
    for (const [key, value] of Object.entries(fields))
      if (!(key in pending)) (saved as Record<string, unknown>)[key] = value
  }

  /** 服务端存下了 `written`：它们成了基准；这期间人没再改的那几格不用再存。 */
  function confirm(written: TaskDraftFields) {
    saved = { ...saved, ...written }
    for (const [key, value] of Object.entries(written))
      if (key in pending && same((pending as Record<string, unknown>)[key], value))
        delete (pending as Record<string, unknown>)[key]
  }

  /** 建这份草稿。改题时是这道题的那份修改，从题目现在的样子开（表单就是照它装的），人改的
   *  几格随后照常存；发题时是空间里一份新的，带着表单现在的样子。 */
  async function create(at: number) {
    const taskId = options.taskId?.() ?? null
    if (taskId) {
      const row = await TaskDraftsApi.forTask(taskId)
      if (at !== epoch) return
      draft.value = row
      options.started?.(row)
      return
    }
    const spaceId = options.spaceId()
    if (!spaceId) throw new Error('no space to start a draft in')
    const written = { ...saved, ...(form?.values() ?? {}), ...pending }
    const row = await TaskDraftsApi.start(spaceId, written)
    if (at !== epoch) return
    draft.value = row
    confirm(written)
    options.started?.(row)
  }

  /** 队里的一件：要建（或有改动要存）就先建这份草稿，再把 `pending` 存进去。 */
  async function sync(at: number, mustCreate: boolean) {
    if (at !== epoch || published.value) return
    try {
      if (!draft.value) {
        if (!mustCreate && !Object.keys(pending).length) return
        await create(at)
        if (at !== epoch || !draft.value) return
      }
      const batch = { ...pending }
      if (Object.keys(batch).length && !published.value) {
        const row = draft.value
        const done = await TaskDraftsApi.save(row.id, batch)
        if (at !== epoch) return
        draft.value = { ...row, version: done.version, updatedAt: done.updatedAt }
        confirm(batch)
      }
      failed = false
    } catch (error) {
      if (at === epoch) failed = true
      throw error
    }
  }

  /** 排进队：前面的做完才轮到它。 */
  function enqueue(mustCreate: boolean): Promise<void> {
    const at = epoch
    inFlight += 1
    refresh()
    const run = queue.then(() => sync(at, mustCreate))
    queue = run.then(
      () => undefined,
      () => undefined
    )
    return run.finally(() => {
      inFlight -= 1
      refresh()
    })
  }

  /** 要一份草稿（问芝士之前、发布之前）：还没有就建，人还没存的改动随后存进去。 */
  async function ensure(): Promise<TaskDraft> {
    if (timer) clearTimeout(timer)
    timer = null
    await enqueue(true)
    if (!draft.value) throw new Error('the draft was not created')
    return draft.value
  }

  /** 表单变了：和要存下的比，人改了的那几格记下来，停手以后存。 */
  function changed(fields: TaskDraftFields) {
    if (published.value || applying) return
    let any = false
    for (const [key, value] of Object.entries(fields) as [TaskDraftField, unknown][]) {
      if (previewing.value.has(key)) continue
      const known = key in pending ? (pending as Record<string, unknown>)[key] : saved[key]
      if (same(value, known)) continue
      // 改回了已存的样子：队里没有在存的，这一格就不用存了；有，就照样排上（盖过在存的那一版）。
      if (!inFlight && same(value, saved[key])) delete (pending as Record<string, unknown>)[key]
      else (pending as Record<string, unknown>)[key] = value
      any = true
      // 人动过的格子，就不再是「芝士已填写」。
      for (const [agentKey, targets] of Object.entries(AGENT_FIELDS))
        if (targets.includes(key) && agentKey in marks.value) {
          const next = { ...marks.value }
          delete next[agentKey]
          marks.value = next
        }
    }
    if (!any) return
    refresh()
    if (timer) clearTimeout(timer)
    timer = setTimeout(() => void flush(), SAVE_AFTER_MS)
  }

  /** 把还没存的都存掉（停手后、发布前、切走前、离开时）：排在队里正在做的后面。 */
  async function flush(): Promise<void> {
    if (timer) clearTimeout(timer)
    timer = null
    try {
      await enqueue(false)
    } catch {
      // 没存成：改动留在 `pending` 里，页面显示没存上，下一次接着存。
    }
  }

  // ---- 芝士 ---------------------------------------------------------------------

  /** 这次调用写的是不是眼前这份草稿。 */
  function aimsHere(args: Record<string, unknown>): boolean {
    const here = draft.value
    if (typeof args.draft === 'number') return here?.id === args.draft
    if (typeof args.task === 'number') return here?.taskId === args.task
    return false
  }

  let lastApplied = ''

  /** 一次 `cheese_problem(action=write)` 的参数写到了这里：能确定的几格先填上。 */
  async function preview(args: unknown, terms: DraftTerms, held: string | null = null) {
    if (!form || !args || typeof args !== 'object' || !aimsHere(args as Record<string, unknown>)) return
    const shown = previewOf(args, terms, typeof saved.descriptionMarkdown === 'string' ? saved.descriptionMarkdown : '')
    // 停在一格前面时，指针已经先过去了。
    const aim = held && held in AGENT_FIELDS ? held : held === 'replace' ? 'description' : null
    writing.value = aim ?? shown.writing
    holding.value = aim !== null && held !== 'replace'
    passage.value = shown.passage ?? null
    const keys = Object.keys(shown.fields) as TaskDraftField[]
    if (!keys.length) return
    const next = new Set(previewing.value)
    keys.forEach((k) => next.add(k))
    previewing.value = next
    const key = JSON.stringify(shown.fields)
    if (key === lastApplied) return
    lastApplied = key
    await put(shown.fields)
  }

  /** 调用结束了。报错就把预览收回成已存的样子。 */
  async function ended(failed: boolean) {
    writing.value = null
    passage.value = null
    holding.value = false
    lastApplied = ''
    if (!failed || !form || !previewing.value.size) return
    const back: TaskDraftFields = {}
    for (const key of previewing.value) (back as Record<string, unknown>)[key] = saved[key]
    previewing.value = new Set()
    await put(back)
  }

  /** 芝士写进草稿了：服务端那一份是定数，填进表单、记成已存。 */
  async function settle(edit: AssistantEdit) {
    if (draft.value?.id !== edit.draft) return
    draft.value = { ...draft.value, version: edit.version, name: edit.name || draft.value.name }
    const after: TaskDraftFields = {}
    for (const [key, change] of Object.entries(edit.changed)) (after as Record<string, unknown>)[key] = change.after
    previewing.value = new Set()
    writing.value = null
    passage.value = null
    holding.value = false
    lastApplied = ''
    saved = { ...saved, ...after }
    if (form) {
      const shown = await put(after)
      // 表单装进去以后的样子（详情从 Markdown 变成编辑器的 JSON）也算已存。
      for (const key of Object.keys(after) as TaskDraftField[])
        if (key in shown) (saved as Record<string, unknown>)[key] = shown[key]
      if ('description' in after && shown.descriptionMarkdown !== undefined)
        saved.descriptionMarkdown = shown.descriptionMarkdown
    }
    const next = { ...marks.value }
    for (const [agentKey, targets] of Object.entries(AGENT_FIELDS))
      if (targets.some((k) => k in edit.changed)) next[agentKey] = edit.id
    marks.value = next
  }

  /** 人撤销了芝士写的几格（或又恢复了）：服务端已经以人的名义写好，这里填进表单、记成
   *  已存。这几格是人的了，不再标「芝士已填写」。 */
  async function restored(draftId: number, fields: TaskDraftFields, version: number) {
    if (draft.value?.id !== draftId) return
    draft.value = { ...draft.value, version }
    saved = { ...saved, ...fields }
    const next = { ...marks.value }
    for (const [agentKey, targets] of Object.entries(AGENT_FIELDS))
      if (targets.some((k) => k in fields)) delete next[agentKey]
    marks.value = next
    if (!form) return
    const shown = await put(fields)
    for (const key of Object.keys(fields) as TaskDraftField[])
      if (key in shown) (saved as Record<string, unknown>)[key] = shown[key]
    if ('description' in fields && shown.descriptionMarkdown !== undefined)
      saved.descriptionMarkdown = shown.descriptionMarkdown
  }

  /** 草稿已经发布：这份没了。 */
  function gone() {
    published.value = true
    if (timer) clearTimeout(timer)
    timer = null
    pending = {}
    marks.value = {}
    refresh()
  }

  return {
    draft,
    id,
    saveState,
    previewing,
    writing,
    passage,
    holding,
    marks,
    published,
    bind,
    open,
    baseline,
    ensure,
    changed,
    flush,
    preview,
    ended,
    settle,
    restored,
    gone,
  }
}
