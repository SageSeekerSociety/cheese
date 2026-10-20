// 个人芝士（#2285）在一个地方的对话：题目页上、发布页和改题页上的草稿上。芝士是同一个，
// 能做的事处处一样；地方只决定对话挂在哪（`home`）和问的时候眼前是哪一页（`page`）。
//
// - 题目页：这道题上我和芝士的几段对话，打开时接着最近的一段；「新对话」等第一句话
//   发出去才建。
// - 草稿（发布页、改题页）：草稿记着它的那段对话，打开就接着它。发布以后对话跟着
//   题目走，到题目页还能接着说。
//
// 回答是流式的（server-sent events）：`queued` 在等会话机空出来，`delta` 是文字，
// `tool` 是芝士开始调一个工具，`tool_args` 是那次调用边写边流过来的参数（页面拿它
// 先把表单填上），`tool_failed` 是那次调用报错了，`edit` 是芝士写进了草稿、改了哪几格
// （定数，页面用它覆盖预览），`published` 是它发布了一份草稿，`error` 是没答上来的
// 原因，`done` 收尾。回答在服务端答到底，跟这条连接无关：连接断了就从断的地方接着读。
import { computed, ref } from 'vue'

import { request } from '@/api'
import { followEventStream, getEventStream, type OnEvent, postEventStream, StreamRefused } from '@/api/eventStream'
import { t } from '@/i18n'
import { isCreditRefusal } from '@/lib/creditUsage'
import { refusalText, renderNoticeMessage } from '@/lib/noticeText'

export interface AssistantConversation {
  id: string
  title: string
  lastActiveAt: string
  questions?: number
}

/** 芝士调的一次工具，或者它推理的一段（`name` 是 `thinking`）。`args` 是参数原文，
 *  边写边长；推理没有参数，`ms` 是它推理了多久。 */
export interface AssistantStep {
  call: string
  name: string
  args: string
  state: 'run' | 'done' | 'failed'
  error?: string
  ms?: number
  /** 开始的时刻（本页的钟），算推理用了多久。 */
  startedAt?: number
}

/** 服务端存下的那一轮做了什么（`assistant_messages.parts`）。 */
interface StoredParts {
  steps?: { call: string; name: string; args: string; failed?: string; ms?: number }[]
  edits?: Record<string, unknown>[]
  published?: AssistantPublished[]
}

/** 芝士往草稿里写的一次：改了哪几格（各自原来是什么），哪几格因为人改过而没动，以及
 *  它留给人确认的地方。`undone` 是人已经撤销的格子。 */
export interface AssistantEdit {
  id: number
  draft: number
  taskId: number | null
  created: boolean
  version: number
  name: string
  changed: Record<string, { before: unknown; after: unknown }>
  kept: string[]
  notes: { field: string; text: string }[]
  undone: string[]
}

/** 芝士发布了一份草稿（或把修改保存到了题目上）。 */
export interface AssistantPublished {
  draft: number
  task: { id: number; name: string }
  spaceId: number
  saved: boolean
  approved?: string
}

export interface AssistantMessage {
  role: 'user' | 'assistant'
  text: string
  /** 回答被人停下了；`text` 是停下时已经写出的部分。 */
  stopped?: boolean
  at: string
  /** 随问题交给芝士的文件名：只在这次打开的页面里有。 */
  files?: string[]
  /** 这一轮里芝士调过的工具、写的草稿、发布的题目：只在这次打开的页面里有。 */
  steps?: AssistantStep[]
  edits?: AssistantEdit[]
  published?: AssistantPublished[]
}

/** 对话挂在哪：一道题上，或一份草稿上（草稿记着它的对话）。 */
export type AssistantHome = { kind: 'task'; id: number } | { kind: 'draft'; id: number; conversationId: string | null }

/** 问的时候眼前是哪一页：芝士看到的「当前页面」就是它。 */
export interface AssistantPage {
  kind: 'task' | 'draft'
  id: number
}

/** 页面要知道的那几件事：表单跟着它们动。 */
export interface AssistantHooks {
  /** 一次调用的参数写到了这里（原文）。 */
  toolArgs?: (call: string, name: string, raw: string) => void
  /** 一次调用结束了：做完，或者报错。 */
  toolEnded?: (call: string, name: string, failed: boolean) => void
  edit?: (edit: AssistantEdit) => void
  published?: (published: AssistantPublished) => void
  /** 在草稿上开了一段新对话。 */
  conversation?: (id: string) => void
}

export interface AssistantOptions {
  home: () => AssistantHome | null
  page?: () => AssistantPage | null
  /** 问之前：确保有地方可问（发布页上还没存过的草稿，先存一份）。 */
  prepare?: () => Promise<void>
  on?: AssistantHooks
}

export function useAssistant(opts: AssistantOptions) {
  const on = opts.on ?? {}

  const conversations = ref<AssistantConversation[]>([])
  const current = ref<string | null>(null)
  const messages = ref<AssistantMessage[]>([])
  /** 正在流进来的那一段回答；没有在答时是 null。 */
  const streaming = ref<string | null>(null)
  /** 芝士此刻在用的工具名（`cheese_docs_search` 之类），面板把它说成人话。 */
  const tool = ref<string | null>(null)
  /** 这一轮正在做的几步。 */
  const steps = ref<AssistantStep[]>([])
  const edits = ref<AssistantEdit[]>([])
  const published = ref<AssistantPublished[]>([])
  /** 在等会话机空出来，还没开始答。 */
  const queued = ref(false)
  /** 没答上来、额度用完之类要告诉人的那一句。 */
  const notice = ref<string | null>(null)
  /** 这次被拒是因为额度不够：面板在提示旁给「查看用量」。 */
  const creditRefused = ref(false)
  const loaded = ref(false)
  let editSeq = 0

  const busy = computed(() => streaming.value !== null)
  const title = computed(() => conversations.value.find((c) => c.id === current.value)?.title ?? '')

  async function listConversations() {
    const home = opts.home()
    if (!home) {
      conversations.value = []
      return
    }
    if (home.kind === 'task') {
      const data = await request<{ conversations: AssistantConversation[] }>(
        `/assistant/tasks/${home.id}/conversations`
      )
      conversations.value = data.conversations
      return
    }
    // 草稿只有它记着的那一段。
    const id = current.value ?? home.conversationId
    if (!id) {
      conversations.value = []
      return
    }
    const known = conversations.value.find((c) => c.id === id)
    conversations.value = known ? [known] : [{ id, title: '', lastActiveAt: new Date().toISOString() }]
  }

  async function open(id: string) {
    const data = await request<
      AssistantConversation & {
        messages: (AssistantMessage & { parts?: StoredParts })[]
        answering?: string | null
      }
    >(`/assistant/conversations/${id}`)
    current.value = id
    messages.value = data.messages.map(({ parts, ...message }) => (parts ? { ...message, ...kept(parts) } : message))
    notice.value = null
    const home = opts.home()
    if (home?.kind === 'draft') conversations.value = [{ id, title: data.title, lastActiveAt: data.lastActiveAt }]
    // 打开时还在答（刷新了页面、换了一台设备）：接上，看它接着写。
    const answering = data.answering
    if (answering)
      void follow(
        id,
        (onEvent) => getEventStream(`/assistant/conversations/${id}/answers/${answering}`, onEvent),
        t('tasks.assistant.failed'),
        answering
      )
  }

  /** 打开面板：接着这里最近的那一段；一段都没有就是一段新的。 */
  async function load() {
    if (loaded.value) return
    const home = opts.home()
    loaded.value = true
    if (!home) return
    await listConversations()
    const latest = home.kind === 'draft' ? home.conversationId : conversations.value[0]?.id
    if (latest) await open(latest)
  }

  /** 换了地方（切到另一份草稿）：从头读。 */
  async function reload() {
    loaded.value = false
    current.value = null
    messages.value = []
    conversations.value = []
    notice.value = null
    await load()
  }

  function startNew() {
    if (busy.value) return
    current.value = null
    messages.value = []
    notice.value = null
  }

  async function select(id: string) {
    if (busy.value || id === current.value) return
    await open(id)
  }

  async function ensureConversation(): Promise<string> {
    if (current.value) return current.value
    const home = opts.home()
    if (!home) throw new Error('nowhere to ask')
    const path =
      home.kind === 'task' ? `/assistant/tasks/${home.id}/conversations` : `/assistant/drafts/${home.id}/conversations`
    const data = await request<AssistantConversation>(path, { method: 'POST' })
    conversations.value = home.kind === 'task' ? [data, ...conversations.value] : [data]
    current.value = data.id
    if (home.kind === 'draft') on.conversation?.(data.id)
    return data.id
  }

  async function ask(question: string, fallback: string, files: { id: number; name: string }[] = []) {
    const text = question.trim()
    if (!text || busy.value) return
    notice.value = null
    creditRefused.value = false
    streaming.value = ''
    const at = new Date().toISOString()
    messages.value = [
      ...messages.value,
      { role: 'user', text, at, ...(files.length ? { files: files.map((f) => f.name) } : {}) },
    ]
    // 面板只留服务端存下的东西：被拒的问题不留那一句。
    let asked = false
    try {
      await opts.prepare?.()
      const id = await ensureConversation()
      const page = opts.page?.() ?? null
      await follow(
        id,
        (onEvent) =>
          postEventStream(
            `/assistant/conversations/${id}/ask`,
            { question: text, ...(page ? { page } : {}), ...(files.length ? { files } : {}) },
            onEvent,
            { onOpen: () => (asked = true) }
          ),
        fallback
      )
    } catch (error) {
      if (error instanceof StreamRefused) {
        notice.value = refusalText(error.body, error.body.message || fallback)
        creditRefused.value = isCreditRefusal(error.body)
      } else notice.value = fallback
    } finally {
      if (!asked) messages.value = messages.value.filter((m) => !(m.role === 'user' && m.at === at))
      streaming.value = null
      await listConversations().catch(() => undefined)
    }
  }

  /** 存下的那一轮，读回成面板上的步骤、改动和结果。 */
  function kept(parts: StoredParts): Pick<AssistantMessage, 'steps' | 'edits' | 'published'> {
    return {
      steps: (parts.steps ?? []).map((step) => ({
        call: step.call,
        name: step.name,
        args: step.args,
        ms: step.ms,
        state: step.failed !== undefined ? ('failed' as const) : ('done' as const),
        error: step.failed,
      })),
      edits: (parts.edits ?? []).map(toEdit),
      published: parts.published ?? [],
    }
  }

  function toEdit(payload: Record<string, unknown>): AssistantEdit {
    return {
      id: ++editSeq,
      draft: Number(payload.draft),
      taskId: typeof payload.taskId === 'number' ? payload.taskId : null,
      created: payload.created === true,
      version: Number(payload.version) || 0,
      name: typeof payload.name === 'string' ? payload.name : '',
      changed: (payload.changed as AssistantEdit['changed']) ?? {},
      kept: Array.isArray(payload.kept) ? (payload.kept as string[]) : [],
      notes: Array.isArray(payload.notes) ? (payload.notes as AssistantEdit['notes']) : [],
      undone: [],
    }
  }

  /** 还在跑的那几步算做完了：芝士开始说话、调下一个工具、或答完了。推理的那一段记下
   *  它用了多久。 */
  function settleSteps() {
    let changed = false
    const now = Date.now()
    const next = steps.value.map((step) => {
      if (step.state !== 'run') return step
      changed = true
      if (step.name === 'thinking') return { ...step, state: 'done' as const, ms: now - (step.startedAt ?? now) }
      on.toolEnded?.(step.call, step.name, false)
      return { ...step, state: 'done' as const }
    })
    if (changed) steps.value = next
  }

  function onStep(event: string, payload: Record<string, unknown>) {
    if (event === 'thinking') {
      settleSteps()
      steps.value = [
        ...steps.value,
        { call: `thinking-${steps.value.length}`, name: 'thinking', args: '', state: 'run', startedAt: Date.now() },
      ]
      queued.value = false
    } else if (event === 'tool') {
      settleSteps()
      const call = typeof payload.call === 'string' ? payload.call : `step-${steps.value.length}`
      const name = typeof payload.name === 'string' ? payload.name : ''
      if (!steps.value.some((s) => s.call === call))
        steps.value = [...steps.value, { call, name, args: '', state: 'run' }]
      tool.value = name || null
      queued.value = false
    } else if (event === 'tool_args' && typeof payload.call === 'string' && typeof payload.text === 'string') {
      const at = typeof payload.at === 'number' ? payload.at : 0
      steps.value = steps.value.map((step) => {
        if (step.call !== payload.call) return step
        const args = step.args.slice(0, at) + (payload.text as string)
        on.toolArgs?.(step.call, step.name, args)
        return { ...step, args }
      })
    } else if (event === 'tool_failed' && typeof payload.call === 'string') {
      steps.value = steps.value.map((step) => {
        if (step.call !== payload.call) return step
        on.toolEnded?.(step.call, step.name, true)
        return { ...step, state: 'failed', error: typeof payload.text === 'string' ? payload.text : '' }
      })
    } else if (event === 'edit') {
      const edit = toEdit(payload)
      edits.value = [...edits.value, edit]
      on.edit?.(edit)
    } else if (event === 'published') {
      const done = payload as unknown as AssistantPublished
      published.value = [...published.value, done]
      on.published?.(done)
    }
  }

  /** 读一个问题的回答，直到它答完：连接断了（关了页面、后端重启）就从断的地方接着读，
   *  服务端那边一直在答。答完的回答进对话；没答完的不留半截。 */
  async function follow(
    conversation: string,
    start: (onEvent: OnEvent) => Promise<void>,
    fallback: string,
    question?: string
  ) {
    streaming.value = ''
    tool.value = null
    queued.value = false
    steps.value = []
    edits.value = []
    published.value = []
    let answer = ''
    let failed = false
    let stopped = false
    let done = false
    const onEvent: OnEvent = (event, payload) => {
      if (event === 'queued') {
        queued.value = true
      } else if (event === 'delta' && typeof payload.text === 'string' && payload.text) {
        // 接着读时可能再听到一遍已经有的字：`at` 说这段从第几个字起。
        settleSteps()
        const from = typeof payload.at === 'number' ? payload.at : answer.length
        answer = answer.slice(0, from) + payload.text
        streaming.value = answer
        tool.value = null
        queued.value = false
      } else if (event === 'done') {
        settleSteps()
        done = true
        stopped = payload.stopped === true
      } else if (event === 'error') {
        failed = true
        notice.value = renderNoticeMessage(
          payload.i18n,
          typeof payload.message === 'string' && payload.message ? payload.message : fallback
        )
      } else onStep(event, payload)
    }
    try {
      await followEventStream(
        start,
        (asked, after) =>
          `/assistant/conversations/${conversation}/answers/${asked}?after=${encodeURIComponent(after)}`,
        onEvent,
        { question }
      )
    } catch (error) {
      if (error instanceof StreamRefused && !question) throw error
      failed = true
      notice.value = fallback
    } finally {
      settleSteps()
      const did = steps.value.length > 0 || edits.value.length > 0 || published.value.length > 0
      if (current.value === conversation && ((answer && !failed && done) || stopped || did))
        messages.value = [
          ...messages.value,
          {
            role: 'assistant',
            text: answer.trim(),
            stopped,
            at: new Date().toISOString(),
            steps: steps.value,
            edits: edits.value,
            published: published.value,
          },
        ]
      if (current.value === conversation) {
        streaming.value = null
        tool.value = null
        queued.value = false
        steps.value = []
        edits.value = []
        published.value = []
      }
    }
  }

  /** 人撤销了一次写入里的某一格：面板把它划掉。 */
  function markUndone(editId: number, field: string) {
    const strike = (list: AssistantEdit[] | undefined) =>
      list?.map((e) => (e.id === editId && !e.undone.includes(field) ? { ...e, undone: [...e.undone, field] } : e))
    edits.value = strike(edits.value) ?? []
    messages.value = messages.value.map((m) => (m.edits ? { ...m, edits: strike(m.edits) } : m))
  }

  /** 停下正在答的这一个：等候中的直接不答了，写了一半的留下这一半。 */
  async function stop() {
    const id = current.value
    if (!id || !busy.value) return
    await request(`/assistant/conversations/${id}/stop`, { method: 'POST' })
  }

  return {
    conversations,
    current,
    messages,
    streaming,
    tool,
    steps,
    edits,
    published,
    queued,
    notice,
    creditRefused,
    busy,
    title,
    load,
    reload,
    startNew,
    select,
    ask,
    stop,
    markUndone,
  }
}
