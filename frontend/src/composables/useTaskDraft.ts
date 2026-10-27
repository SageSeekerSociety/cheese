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
  let saving: Promise<void> | null = null
  let starting: Promise<TaskDraft> | null = null
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

  /** 打开一份草稿（或者还没有草稿：从空白开始）。 */
  function open(row: TaskDraft | null, fields: TaskDraftFields = {}) {
    draft.value = row
    saved = { ...(row?.fields ?? fields) }
    pending = {}
    marks.value = {}
    previewing.value = new Set()
    writing.value = null
    published.value = false
    saveState.value = row ? 'saved' : 'idle'
  }

  /** 页面刚把这些装进表单（表单刚挂上、附件刚读回来）：它们就是起点，不算人改的。 */
  function baseline(fields: TaskDraftFields) {
    for (const [key, value] of Object.entries(fields))
      if (!(key in pending)) (saved as Record<string, unknown>)[key] = value
  }

  /** 建这份草稿，写进 `written`。改题时是这道题的那份修改：从题目现在的样子开，再存进
   *  人改的几格；发题时是空间里一份新的。 */
  async function create(taskId: number | null, written: TaskDraftFields): Promise<TaskDraft> {
    if (taskId) {
      const row = await TaskDraftsApi.forTask(taskId)
      if (!Object.keys(written).length) return row
      const done = await TaskDraftsApi.save(row.id, written)
      return { ...row, fields: { ...row.fields, ...written }, version: done.version, updatedAt: done.updatedAt }
    }
    const spaceId = options.spaceId()
    if (!spaceId) throw new Error('no space to start a draft in')
    return TaskDraftsApi.start(spaceId, written)
  }

  /** 还没有草稿就建一份，带上 `extra`（还没存的那几格）。 */
  async function ensure(extra: TaskDraftFields = {}): Promise<TaskDraft> {
    if (draft.value) return draft.value
    if (starting) return starting
    const taskId = options.taskId?.() ?? null
    // 改题页的表单就是照题目现在的样子装的，只有人改的几格要写；新题目带上表单的全部。
    const written = taskId ? { ...pending, ...extra } : { ...saved, ...(form?.values() ?? {}), ...pending, ...extra }
    starting = create(taskId, written).then((row) => {
      draft.value = row
      saved = { ...saved, ...written }
      pending = {}
      saveState.value = 'saved'
      options.started?.(row)
      return row
    })
    try {
      return await starting
    } finally {
      starting = null
    }
  }

  /** 表单变了：和已存的比，人改了的那几格记下来，停手以后存。 */
  function changed(fields: TaskDraftFields) {
    if (published.value || applying) return
    let any = false
    for (const [key, value] of Object.entries(fields) as [TaskDraftField, unknown][]) {
      if (previewing.value.has(key) || same(value, saved[key])) continue
      ;(pending as Record<string, unknown>)[key] = value
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
    if (timer) clearTimeout(timer)
    timer = setTimeout(() => void flush(), SAVE_AFTER_MS)
  }

  /** 把还没存的都存掉（发布前、切走前也调它）。 */
  async function flush(): Promise<void> {
    if (timer) clearTimeout(timer)
    timer = null
    if (saving) await saving
    const fields = pending
    if (!Object.keys(fields).length || published.value) return
    pending = {}
    saveState.value = 'saving'
    saving = (async () => {
      try {
        if (!draft.value) {
          // 建草稿时就带上了这几格。
          await ensure(fields)
        } else {
          const row = draft.value
          const done = await TaskDraftsApi.save(row.id, fields)
          if (draft.value?.id === row.id)
            draft.value = { ...draft.value, version: done.version, updatedAt: done.updatedAt }
        }
        saved = { ...saved, ...fields }
        saveState.value = 'saved'
      } catch {
        pending = { ...fields, ...pending }
        saveState.value = 'failed'
      }
    })()
    try {
      await saving
    } finally {
      saving = null
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
    pending = {}
    marks.value = {}
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
