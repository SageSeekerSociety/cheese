// 个人芝士（#2285）和一个人的那一条对话。芝士是同一个，在哪一页打开都是这一条：每段
// 对话是其中的一个话题，「新话题」开下一个，之前的话题留在上面，往上翻能看到。问的时候
// 眼前是哪一页（`page`）随问题一起说，页面不再决定对话挂在哪。全站只有一份，由芝士的
// 宿主持有（`stores/agent.ts`）。
//
// 回答是流式的（server-sent events）：`queued` 在等会话机空出来，`delta` 是文字，
// `tool` 是芝士开始调一个工具，`tool_args` 是那次调用边写边流过来的参数（页面拿它
// 先把表单填上），`tool_done` 是那次调用回来了，`tool_failed` 是那次调用报错了，`change` 是芝士写进了草稿、改了哪几格
// （定数，页面用它覆盖预览；服务端记下了它，撤销按它的编号来），`published` 是它发布了一份草稿，
// `proposal` 是它替人起草了一条要发到项目里的消息、或一个要开的任务（人确认后才发出），`error` 是没答上来的
// 原因，`done` 收尾。回答在服务端答到底，跟这条连接无关：连接断了就从断的地方接着读。
import { computed, ref } from 'vue'

import { type AgentChange, AgentChangesApi, type AssistantCall, request } from '@/api'
import { followEventStream, getEventStream, type OnEvent, postEventStream, StreamRefused } from '@/api/eventStream'
import { t } from '@/i18n'
import { isCreditRefusal } from '@/lib/creditUsage'
import { type NoticeMessage, refusalText, renderNoticeMessage } from '@/lib/noticeText'

export interface AssistantConversation {
  id: string
  title: string
  lastActiveAt: string
  /** 例行事务的一次运行：平台开的，不是这个人问的。 */
  routine?: boolean
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
  /** 它之前回答写到了第几个字：面板把它排在这些字后面。 */
  at?: number
  /** 它读到的东西叫什么（「频道「综合」」）：回来时才知道，显示在编号的位置。 */
  shown?: string
}

/** 芝士派出的一个分身：派它的那次调用（`call`）、它做什么、做到哪了、最近做的几步。 */
export interface AssistantSubagent {
  id: string
  call: string
  description: string
  status: 'running' | 'done' | 'failed' | 'stopped'
  steps: { name: string; args: string; shown?: string }[]
}

/** 服务端存下的那一轮做了什么（`assistant_messages.parts`）。 */
interface StoredParts {
  steps?: { call: string; name: string; args: string; failed?: string; ms?: number; at?: number; shown?: string }[]
  /** 做了事，却一个字没写。 */
  silent?: boolean
  /** 这一轮派出的分身，最后是什么样。 */
  subagents?: AssistantSubagent[]
  edits?: Record<string, unknown>[]
  published?: AssistantPublished[]
  proposals?: AssistantProposal[]
  /** 问题带的文件名。 */
  files?: string[]
  /** 平台开始一次运行的那一句（`role` 为 `platform`）：哪条自动化、发生了什么。 */
  shown?: AssistantShown
  /** 芝士交给人的文件。 */
  handed?: AssistantHanded[]
  rules?: AssistantRule[]
  /** 问题是在哪一页问的（那一页的名字）。 */
  page?: string
  /** 问题带着的、人在页面上选中的那段字。 */
  selection?: string
  /** 问题里 `<#id>` 引用的东西。 */
  cited?: AssistantCited[]
}

/** 问题里引用的一样东西：`<#id>` 里的 id、种类和名字。 */
export interface AssistantCited {
  id: string
  kind: 'channel' | 'task' | 'thread' | 'doc' | 'file' | 'problem'
  title: string
}

/** 芝士交给人的一份文件（`cheese_library(action=give)`）：存在他自己的资料库里——他名下那个团队
 *  （`team`）的，叫 `path`。 */
export interface AssistantHanded {
  team: number
  path: string
  bytes: number
}

/** 芝士建或改的一条自动化（`cheese_routine`），回答里的那张卡片：什么时候、做什么。
 *  它现在怎样（等待中、已开始……）按 `id` 另读。 */
export interface AssistantRule {
  id: string
  when: string
  note: string
}

/** 平台说的那一句在面板上怎么写：哪条自动化开始运行，或这段对话里等的事到了——任务的
 *  审阅（`task`、`event`）时说是哪件事。 */
export interface AssistantShown {
  routine?: string
  wait?: string
  task?: string
  event?: string
}

/** 芝士往草稿里写的一次：改了哪几格（各自原来是什么），哪几格因为人改过而没动，以及
 *  它留给人确认的地方。`undone` 是人已经撤销的格子。`undoable` 是服务端记下了这一次
 *  （`id` 是它的编号）；更早的回答里的写入没有记下，只给看。 */
export interface AssistantEdit {
  id: number
  /** `draft` 是写题目草稿；`settings`、`notify` 是改这个人自己的通知设置、频道提醒
   *  （`title` 是卡头，`changed` 每一项带着它的名字 `label`）；`memory` 是芝士记下、
   *  改写或忘掉的一条记忆（`changed.memory` 的 `before`、`after` 是前后的全文）。 */
  kind: string
  title: string
  undoable: boolean
  draft: number
  taskId: number | null
  created: boolean
  version: number
  name: string
  /** 写完这一次，草稿上的题怎么开始：卡上队伍锁定、日期两格按它说。 */
  startMode?: 'DIRECT' | 'APPLY'
  changed: Record<string, { before: unknown; after: unknown }>
  kept: string[]
  notes: { field: string; text: string }[]
  /** 照这次写入之后草稿的样子，存下它的阶段会让几个人被判未通过、几个人回到进行中（或
   *  保存会被拒的那句），按句子的键和参数给，读的人用自己的语言读。空 = 没人受影响。 */
  stageEffect: NoticeMessage[]
  undone: string[]
}

/** 芝士替人起草、等人确认的一件事：一条消息（发到频道、任务或支线），或一个要开的任务
 *  （标题和交代，交代作为任务里的第一条消息发进去）。`request` 是发这条消息用的请求号：
 *  重发只落一条。`state` 是还在等（pending）、已发出（sent，`result` 是发出的那条消息或
 *  开出的任务），或人说不用了（dismissed）。 */
export interface AssistantProposal {
  id: number
  kind: 'message' | 'task' | 'act'
  /** `expired`：例行事务运行时留下的，过了保留期或这条例行事务又运行了一次。 */
  state: 'pending' | 'sent' | 'dismissed' | 'expired'
  request: string
  project: string
  topic?: string
  topicName?: string
  text?: string
  channel?: string
  channelName?: string
  title?: string
  brief?: string
  /** `act`：要做的事（把关读到的那句话）、为什么先问人、确认后的调用。 */
  summary?: string
  reason?: string
  calls?: AssistantCall[]
  result?: { topic?: string; message?: string; task?: string; done?: string }
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
  role: 'user' | 'assistant' | 'platform'
  text: string
  /** 在话题里的顺序号：往上翻时从最早读到的那一条往前读。这一页刚说的没有。 */
  seq?: number
  /** 回答被人停下了；`text` 是停下时已经写出的部分。 */
  stopped?: boolean
  at: string
  /** 随问题交给芝士的文件名：只在这次打开的页面里有。 */
  files?: string[]
  shown?: AssistantShown
  /** 这一轮里芝士调过的工具、写的草稿、发布的题目：只在这次打开的页面里有。 */
  steps?: AssistantStep[]
  edits?: AssistantEdit[]
  published?: AssistantPublished[]
  proposals?: AssistantProposal[]
  handed?: AssistantHanded[]
  rules?: AssistantRule[]
  /** 做了事，却一个字没写：面板说一句，不留一段空白。 */
  silent?: boolean
  /** 这一轮派出的分身。 */
  subagents?: AssistantSubagent[]
  /** 人问的：在哪一页问的、带着选中的哪段字、引用了什么。 */
  askedOn?: string
  selection?: string
  cited?: AssistantCited[]
}

/** 问一句时除了问题本身还交什么：请求里多带的几样，和这一句在面板上怎么写。 */
export interface AssistantAsking {
  body: Record<string, unknown>
  shown: Pick<AssistantMessage, 'askedOn' | 'selection' | 'cited'>
}

/** 问的时候眼前是哪一页：芝士看到的「当前页面」就是它。题目和草稿按编号，项目里的频道、
 *  任务、支线（topic）、文档（doc）和项目本身（project）按 UUID。 */
/** 交给芝士的一个文件：消息附件，或个人资料库里的一份。 */
export type HandedFile = { id: number; name: string } | { path: string; name: string }

/** 芝士答着时插进去的一句话，那一轮结束前芝士没听到（服务端原样交回）。 */
export interface Unheard {
  text?: string
  files?: HandedFile[]
}

export interface AssistantPage {
  kind: 'task' | 'draft' | 'topic' | 'doc' | 'project' | 'library'
  id: number | string
}

/** 页面要知道的那几件事：表单跟着它们动。 */
export interface AssistantHooks {
  /** 一次调用的参数写到了这里（原文）。 */
  toolArgs?: (call: string, name: string, raw: string) => void
  /** 一次调用结束了：做完，或者报错。 */
  toolEnded?: (call: string, name: string, failed: boolean) => void
  edit?: (edit: AssistantEdit) => void
  published?: (published: AssistantPublished) => void
}

/** 之前的一个话题：「新话题」以前说的，或往上翻读回来的。 */
export interface AssistantTopic {
  id: string
  title: string
  lastActiveAt: string
  messages: AssistantMessage[]
  /** 这个话题还有更早的消息没读回来。 */
  earlier?: boolean
}

export interface AssistantOptions {
  /** 这一问挂在哪一页；`null` 是这一问不带页面（`no_page`）。在 `prepare` 之后读：发布页、
   *  改题页上的草稿是 `prepare` 才建出来的。 */
  page?: () => AssistantPage | null
  /** 问之前：确保有地方可问（发布页上还没存过的草稿，先存一份）。 */
  prepare?: () => Promise<void>
  on?: AssistantHooks
  /** 这一句还带什么（不带页面、选中的字、引用），每问一句读一次。 */
  asking?: (question: string) => AssistantAsking
}

export function createAssistant(opts: AssistantOptions = {}) {
  const on = opts.on ?? {}

  /** 这个人的话题，最近的在前：读回来的那几页。 */
  const conversations = ref<AssistantConversation[]>([])
  /** 下一页话题从哪里读起；null 是没有更早的了。 */
  const nextPage = ref<string | null>(null)
  /** 当前话题还有更早的消息没读回来。 */
  const currentEarlier = ref(false)
  /** 当前话题上面摆着的之前几个话题，按时间从早到晚。 */
  const earlier = ref<AssistantTopic[]>([])
  const current = ref<string | null>(null)
  const messages = ref<AssistantMessage[]>([])
  /** 正在流进来的那一段回答；没有在答时是 null。 */
  const streaming = ref<string | null>(null)
  /** 芝士此刻在用的工具名（`cheese_help` 之类），面板把它说成人话。 */
  const tool = ref<string | null>(null)
  /** 这一轮正在做的几步。 */
  const steps = ref<AssistantStep[]>([])
  const edits = ref<AssistantEdit[]>([])
  const published = ref<AssistantPublished[]>([])
  const proposals = ref<AssistantProposal[]>([])
  /** 这一轮交出来的文件。 */
  const handed = ref<AssistantHanded[]>([])
  const rules = ref<AssistantRule[]>([])
  /** 这一轮派出的分身，做到哪了。 */
  const subagents = ref<AssistantSubagent[]>([])
  /** 在等会话机空出来，还没开始答。 */
  const queued = ref(false)
  /** 没答上来、额度用完之类要告诉人的那一句。 */
  const notice = ref<string | null>(null)
  /** 这次被拒是因为额度不够：面板在提示旁给「查看用量」。 */
  const creditRefused = ref(false)
  const loaded = ref(false)
  let editSeq = 0

  const busy = computed(() => streaming.value !== null)

  /** 还有没摆出来的更早的话题：往上翻能读。 */
  const hasOlder = computed(() => {
    const shown = new Set([current.value, ...earlier.value.map((topic) => topic.id)])
    return nextPage.value !== null || conversations.value.some((c) => !shown.has(c.id))
  })

  type ConversationPage = { conversations: AssistantConversation[]; next?: string | null }

  /** 读最近的一页话题，替换掉之前读的。 */
  async function listConversations() {
    const data = await request<ConversationPage>('/assistant/conversations')
    conversations.value = data.conversations ?? []
    nextPage.value = data.next ?? null
  }

  /** 接着读下一页更早的话题。 */
  async function morePage() {
    if (nextPage.value === null) return
    const data = await request<ConversationPage>(
      `/assistant/conversations?before=${encodeURIComponent(nextPage.value)}`
    )
    const known = new Set(conversations.value.map((c) => c.id))
    conversations.value = [...conversations.value, ...(data.conversations ?? []).filter((c) => !known.has(c.id))]
    nextPage.value = data.next ?? null
  }

  type ReadMessage = AssistantMessage & { parts?: StoredParts }
  type ReadConversation = AssistantConversation & {
    messages: ReadMessage[]
    answering?: string | null
    /** 还有更早的消息。 */
    earlier?: boolean
  }

  async function read(id: string): Promise<ReadConversation> {
    return request<ReadConversation>(`/assistant/conversations/${id}`)
  }

  const shownMessages = (data: { messages: ReadMessage[] }) =>
    data.messages.map(({ parts, ...message }) => (parts ? { ...message, ...kept(parts) } : message))

  async function open(id: string) {
    const data = await read(id)
    current.value = id
    messages.value = shownMessages(data)
    currentEarlier.value = data.earlier === true
    notice.value = null
    // 打开时还在答（刷新了页面、换了一台设备）：接上，看它接着写。
    const answering = data.answering
    if (answering)
      void follow(
        id,
        (onEvent) => getEventStream(`/assistant/conversations/${id}/answers/${answering}`, onEvent),
        t('tasks.assistant.failed'),
        answering
      ).then((unheard) => askUnheard(unheard, t('tasks.assistant.failed')))
  }

  /** 打开面板：接着最近的那个话题；一个都没有，就从第一句话开始一个新的。只读一次，
   *  之后面板关了再开还是这一条。 */
  async function load() {
    if (loaded.value) return
    loaded.value = true
    await listConversations()
    const latest = conversations.value[0]?.id
    if (latest) await open(latest)
  }

  /** 换了一个人（退出登录）：之前的对话一样不留。 */
  function reset() {
    loaded.value = false
    conversations.value = []
    nextPage.value = null
    earlier.value = []
    current.value = null
    messages.value = []
    currentEarlier.value = false
    notice.value = null
    creditRefused.value = false
  }

  /** 「新话题」：之前说的留在上面，芝士从下一句话起不再读它们。话题等第一句话发出去才建。 */
  function startNew() {
    if (busy.value) return
    if (current.value) {
      const at = conversations.value.find((c) => c.id === current.value)
      earlier.value = [
        ...earlier.value,
        {
          id: current.value,
          title: at?.title ?? '',
          lastActiveAt: at?.lastActiveAt ?? new Date().toISOString(),
          messages: messages.value,
          earlier: currentEarlier.value,
        },
      ]
    }
    current.value = null
    messages.value = []
    currentEarlier.value = false
    notice.value = null
  }

  /** 打开指定的一段对话（通知、设置页里的「查看对话」）：眼前这段收到上面，像「新话题」一样。 */
  async function show(id: string) {
    loaded.value = true
    if (current.value === id) return open(id)
    if (busy.value) return
    startNew()
    earlier.value = earlier.value.filter((topic) => topic.id !== id)
    await open(id)
    await listConversations().catch(() => undefined)
  }

  /** 一个话题（`null` 是当前这个）更早的那一页消息：读回来接在它最上面。 */
  async function earlierMessages(id: string | null) {
    const topic = id === null ? null : earlier.value.find((t) => t.id === id)
    const conversation = id ?? current.value
    const shown = topic ? topic.messages : messages.value
    const first = shown.find((m) => typeof m.seq === 'number')?.seq
    if (!conversation || first === undefined) return
    const data = await request<{ messages: ReadMessage[]; earlier?: boolean }>(
      `/assistant/conversations/${conversation}/messages?before=${first}`
    )
    const more = shownMessages(data)
    if (topic)
      earlier.value = earlier.value.map((t) =>
        t.id === id ? { ...t, messages: [...more, ...t.messages], earlier: data.earlier === true } : t
      )
    else {
      messages.value = [...more, ...messages.value]
      currentEarlier.value = data.earlier === true
    }
  }

  /** 往上翻：最上面那个话题还有更早的消息，先读它的；读完了，再把还没摆出来的、最近的那个
   *  更早的话题读回来，摆在最上面。读过的那几页里没有了，就接着读下一页话题。 */
  async function older() {
    const top = earlier.value[0]
    if (top ? top.earlier : currentEarlier.value) return earlierMessages(top ? top.id : null)
    const unshown = () => {
      const shown = new Set([current.value, ...earlier.value.map((topic) => topic.id)])
      return conversations.value.find((c) => !shown.has(c.id))
    }
    let next = unshown()
    while (!next && nextPage.value !== null) {
      await morePage()
      next = unshown()
    }
    if (!next) return
    const data = await read(next.id)
    earlier.value = [
      {
        id: next.id,
        title: data.title,
        lastActiveAt: data.lastActiveAt,
        messages: shownMessages(data),
        earlier: data.earlier === true,
      },
      ...earlier.value,
    ]
  }

  async function ensureConversation(): Promise<string> {
    if (current.value) return current.value
    const data = await request<AssistantConversation>('/assistant/conversations', { method: 'POST' })
    conversations.value = [data, ...conversations.value]
    current.value = data.id
    return data.id
  }

  /** `kept`：这句话已经在对话里了（芝士答着时插的话，没等它听到那一轮就结束了），
   *  只问，不再记一遍。 */
  async function ask(question: string, fallback: string, files: HandedFile[] = [], kept = false) {
    const text = question.trim()
    if ((!text && !files.length) || busy.value) return
    notice.value = null
    creditRefused.value = false
    streaming.value = ''
    const at = new Date().toISOString()
    const asking = opts.asking?.(text)
    if (!kept)
      messages.value = [
        ...messages.value,
        { role: 'user', text, at, ...(files.length ? { files: files.map((f) => f.name) } : {}), ...asking?.shown },
      ]
    // 面板只留服务端存下的东西：被拒的问题不留那一句。
    let asked = false
    let unheard: Unheard[] = []
    try {
      await opts.prepare?.()
      const id = await ensureConversation()
      const page = opts.page?.() ?? null
      unheard = await follow(
        id,
        (onEvent) =>
          postEventStream(
            `/assistant/conversations/${id}/ask`,
            {
              question: text,
              ...(page ? { page } : opts.page ? { no_page: true } : {}),
              ...(files.length ? { files } : {}),
              ...asking?.body,
              ...(kept ? { kept: true } : {}),
            },
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
      if (!asked && !kept) messages.value = messages.value.filter((m) => !(m.role === 'user' && m.at === at))
      streaming.value = null
      await listConversations().catch(() => undefined)
    }
    await askUnheard(unheard, fallback)
  }

  /** 插进那一轮的话，那一轮结束前芝士没听到：合成一个问题接着问。 */
  async function askUnheard(unheard: Unheard[], fallback: string) {
    if (!unheard.length) return
    const text = unheard
      .map((u) => u.text ?? '')
      .filter(Boolean)
      .join('\n\n')
    await ask(
      text,
      fallback,
      unheard.flatMap((u) => u.files ?? []),
      true
    )
  }

  /** 存下的那一轮，读回成面板上的步骤、改动和结果。 */
  function kept(
    parts: StoredParts
  ): Pick<
    AssistantMessage,
    | 'steps'
    | 'edits'
    | 'published'
    | 'proposals'
    | 'files'
    | 'shown'
    | 'handed'
    | 'rules'
    | 'silent'
    | 'subagents'
    | 'askedOn'
    | 'selection'
    | 'cited'
  > {
    return {
      ...(parts.page ? { askedOn: parts.page } : {}),
      ...(parts.selection ? { selection: parts.selection } : {}),
      ...(parts.cited?.length ? { cited: parts.cited } : {}),
      ...(parts.subagents?.length ? { subagents: parts.subagents } : {}),
      ...(parts.files?.length ? { files: parts.files } : {}),
      ...(parts.silent ? { silent: true } : {}),
      ...(parts.shown ? { shown: parts.shown } : {}),
      steps: (parts.steps ?? []).map((step) => ({
        call: step.call,
        name: step.name,
        args: step.args,
        ms: step.ms,
        ...(typeof step.at === 'number' ? { at: step.at } : {}),
        ...(step.shown ? { shown: step.shown } : {}),
        state: step.failed !== undefined ? ('failed' as const) : ('done' as const),
        error: step.failed,
      })),
      edits: (parts.edits ?? []).map(toEdit),
      published: parts.published ?? [],
      proposals: parts.proposals ?? [],
      handed: parts.handed ?? [],
      rules: parts.rules ?? [],
    }
  }

  function toEdit(payload: Record<string, unknown>): AssistantEdit {
    const kept = typeof payload.id === 'number'
    return {
      id: kept ? (payload.id as number) : -++editSeq,
      kind: typeof payload.kind === 'string' ? payload.kind : 'draft',
      title: typeof payload.title === 'string' ? payload.title : '',
      undoable: kept,
      draft: Number(payload.draft),
      taskId: typeof payload.taskId === 'number' ? payload.taskId : null,
      created: payload.created === true,
      version: Number(payload.version) || 0,
      name: typeof payload.name === 'string' ? payload.name : '',
      startMode: payload.startMode === 'APPLY' || payload.startMode === 'DIRECT' ? payload.startMode : undefined,
      changed: (payload.changed as AssistantEdit['changed']) ?? {},
      kept: Array.isArray(payload.kept) ? (payload.kept as string[]) : [],
      notes: Array.isArray(payload.notes) ? (payload.notes as AssistantEdit['notes']) : [],
      stageEffect: Array.isArray(payload.stageEffect) ? (payload.stageEffect as NoticeMessage[]) : [],
      undone: Array.isArray(payload.undone) ? (payload.undone as string[]) : [],
    }
  }

  /** 还在跑的那几步算做完了：芝士开始说话、开始推理、或答完了。推理的那一段记下它用了
   *  多久。`thinkingOnly`：只结束推理——芝士接着调下一个工具时，同时调的那几个还在跑，
   *  它们各自回来时（`tool_done`）才算完。 */
  function settleSteps(thinkingOnly = false) {
    let changed = false
    const now = Date.now()
    const next = steps.value.map((step) => {
      if (step.state !== 'run' || (thinkingOnly && step.name !== 'thinking')) return step
      changed = true
      if (step.name === 'thinking') return { ...step, state: 'done' as const, ms: now - (step.startedAt ?? now) }
      on.toolEnded?.(step.call, step.name, false)
      return { ...step, state: 'done' as const }
    })
    if (changed) steps.value = next
  }

  function onStep(event: string, payload: Record<string, unknown>) {
    const at = typeof payload.at === 'number' ? { at: payload.at } : {}
    if (event === 'thinking') {
      settleSteps()
      steps.value = [
        ...steps.value,
        {
          call: `thinking-${steps.value.length}`,
          name: 'thinking',
          args: '',
          state: 'run',
          startedAt: Date.now(),
          ...at,
        },
      ]
      queued.value = false
    } else if (event === 'tool') {
      settleSteps(true)
      const call = typeof payload.call === 'string' ? payload.call : `step-${steps.value.length}`
      const name = typeof payload.name === 'string' ? payload.name : ''
      if (!steps.value.some((s) => s.call === call))
        steps.value = [...steps.value, { call, name, args: '', state: 'run', ...at }]
      tool.value = name || null
      queued.value = false
    } else if (event === 'tool_done' && typeof payload.call === 'string') {
      steps.value = steps.value.map((step) => {
        if (step.call !== payload.call || step.state !== 'run') return step
        on.toolEnded?.(step.call, step.name, false)
        const shown = typeof payload.shown === 'string' && payload.shown ? { shown: payload.shown } : {}
        return { ...step, ...shown, state: 'done' }
      })
      if (!steps.value.some((step) => step.state === 'run')) tool.value = null
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
    } else if (event === 'change') {
      const edit = toEdit(payload)
      edits.value = [...edits.value, edit]
      on.edit?.(edit)
    } else if (event === 'subagents' && Array.isArray(payload.branches)) {
      subagents.value = payload.branches as AssistantSubagent[]
    } else if (event === 'handed') {
      handed.value = [...handed.value, payload as unknown as AssistantHanded]
    } else if (event === 'rule') {
      const rule = payload as unknown as AssistantRule
      rules.value = [...rules.value.filter((r) => r.id !== rule.id), rule]
    } else if (event === 'proposal') {
      proposals.value = [...proposals.value, payload as unknown as AssistantProposal]
    } else if (event === 'published') {
      const done = payload as unknown as AssistantPublished
      published.value = [...published.value, done]
      on.published?.(done)
    }
  }

  /** 读一个问题的回答，直到它答完：连接断了（关了页面、后端重启）就从断的地方接着读，
   *  服务端那边一直在答。答完的回答进对话；没答完的不留半截。交回答着时插进去、芝士
   *  却没听到的话（人按了停止的不算：那是不要它接着做了）。 */
  async function follow(
    conversation: string,
    start: (onEvent: OnEvent) => Promise<void>,
    fallback: string,
    question?: string
  ): Promise<Unheard[]> {
    streaming.value = ''
    tool.value = null
    queued.value = false
    steps.value = []
    edits.value = []
    published.value = []
    proposals.value = []
    handed.value = []
    rules.value = []
    subagents.value = []
    let answer = ''
    let failed = false
    let stopped = false
    let done = false
    let unheard: Unheard[] = []
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
      } else if (event === 'unheard' && Array.isArray(payload.said)) {
        unheard = payload.said as Unheard[]
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
      const did =
        steps.value.length > 0 ||
        edits.value.length > 0 ||
        published.value.length > 0 ||
        proposals.value.length > 0 ||
        handed.value.length > 0 ||
        rules.value.length > 0
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
            proposals: proposals.value,
            handed: handed.value,
            rules: rules.value,
            ...(subagents.value.length ? { subagents: subagents.value } : {}),
            ...(did && done && !failed && !stopped && !answer.trim() ? { silent: true } : {}),
          },
        ]
      if (current.value === conversation) {
        streaming.value = null
        tool.value = null
        queued.value = false
        steps.value = []
        edits.value = []
        published.value = []
        proposals.value = []
        handed.value = []
        rules.value = []
        subagents.value = []
      }
    }
    return stopped || current.value !== conversation ? [] : unheard
  }

  /** 一件起草的事现在怎样了（人发出了，或说不用了）：面板照着改那张卡。 */
  function settleProposal(id: number, state: AssistantProposal['state'], result?: AssistantProposal['result']) {
    const mark = (list: AssistantProposal[]) => list.map((p) => (p.id === id ? { ...p, state, result } : p))
    proposals.value = mark(proposals.value)
    messages.value = messages.value.map((m) => (m.proposals ? { ...m, proposals: mark(m.proposals) } : m))
  }

  /** 服务端说这几次写入现在撤销了哪几格：面板照着划掉或恢复。 */
  function settleChanges(changed: AgentChange[]) {
    const now = new Map(changed.map((c) => [c.id, c.undone]))
    const strike = (list: AssistantEdit[]) =>
      list.map((e) => (now.has(e.id) ? { ...e, undone: now.get(e.id) ?? [] } : e))
    edits.value = strike(edits.value)
    messages.value = messages.value.map((m) => (m.edits ? { ...m, edits: strike(m.edits) } : m))
  }

  /** 撤销一次写入里的一格（芝士的字段名）：服务端写回这一轮之前的样子。 */
  async function undo(changeId: number, part?: string) {
    const done = await AgentChangesApi.undo(changeId, part)
    settleChanges(done.changes)
    return done
  }

  /** 这段对话在别处说了新的话（别的标签页、别的设备、例行事务）：话题列表重读；正开着它、
   *  这里又没在答，就把它最近的那一页重读一遍，往上翻读回来的更早的照旧留着。 */
  async function heard(conversation: string) {
    await listConversations().catch(() => undefined)
    if (conversation !== current.value || busy.value) return
    const data = await read(conversation)
    if (conversation !== current.value || busy.value) return
    const page = shownMessages(data)
    const first = page.find((m) => typeof m.seq === 'number')?.seq
    const before = first === undefined ? [] : messages.value.filter((m) => typeof m.seq === 'number' && m.seq < first)
    messages.value = [...before, ...page]
    if (!before.length) currentEarlier.value = data.earlier === true
    if (data.answering)
      void follow(
        conversation,
        (onEvent) => getEventStream(`/assistant/conversations/${conversation}/answers/${data.answering}`, onEvent),
        t('tasks.assistant.failed'),
        data.answering
      ).then((unheard) => askUnheard(unheard, t('tasks.assistant.failed')))
  }

  /** 芝士正在答时，人又说了一句：存进对话，插进正在答的这一轮，芝士下一步之前读到。
   *  这时恰好答完了，就当作一个新问题问。 */
  async function say(text: string, fallback: string, files: HandedFile[] = []) {
    const words = text.trim()
    const id = current.value
    if (!words && !files.length) return
    if (!id || !busy.value) return ask(words, fallback, files)
    const at = new Date().toISOString()
    const asking = opts.asking?.(words)
    // 插进去的话没换页面：只写它带的字和引用，不写「在哪问的」。
    const shown = {
      ...(asking?.shown.selection ? { selection: asking.shown.selection } : {}),
      ...(asking?.shown.cited ? { cited: asking.shown.cited } : {}),
    }
    messages.value = [
      ...messages.value,
      { role: 'user', text: words, at, ...(files.length ? { files: files.map((f) => f.name) } : {}), ...shown },
    ]
    try {
      await request(`/assistant/conversations/${id}/say`, {
        method: 'POST',
        body: JSON.stringify({
          text: words,
          files,
          ...(asking?.body.selection ? { selection: asking.body.selection } : {}),
        }),
      })
    } catch {
      messages.value = messages.value.filter((m) => !(m.role === 'user' && m.at === at))
      if (!busy.value) return ask(words, fallback, files)
      notice.value = fallback
    }
  }

  /** 面板正给人看着这段对话：这时答完的不再另发通知。看着的时候每隔一会儿说一次。 */
  async function looking() {
    const id = current.value
    if (id) await request(`/assistant/conversations/${id}/looking`, { method: 'POST' })
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
    earlier,
    hasOlder,
    currentEarlier,
    messages,
    streaming,
    tool,
    steps,
    edits,
    published,
    proposals,
    handed,
    rules,
    subagents,
    queued,
    notice,
    creditRefused,
    busy,
    load,
    reset,
    startNew,
    show,
    older,
    earlierMessages,
    heard,
    looking,
    say,
    ask,
    stop,
    undo,
    settleChanges,
    settleProposal,
  }
}
