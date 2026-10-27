// 个人芝士的宿主：全站只有一个芝士、一个面板、一条对话，由这里持有。
//
// 页面不接线，只登记两件事：
// - 上下文（`useAgentContext`）：这一页是什么（芝士读到的「当前页面」由服务端按种类和 id
//   写出），以及面板在这一页的问候、开场问题、输入框提示，能不能交文件。
// - 可写面（`useAgentSurface`，可选）：芝士写进这一页时，页面怎么跟着动——参数边到边填、
//   服务端定稿、撤销，以及面板上那一轮怎么画（改动卡、结果）。有表单的页面再说表单在哪、
//   有哪几格（`form`）：指针、描边、「芝士已填写」由宿主画在表单上，页面不接线。
//
// 两份登记都是栈：后登记的在上面，组件卸载或被 keep-alive 收起时自己撤下（和
// `useScreenHeader` 一个做法）。宿主状态放在模块里，不放在外壳组件里：外壳在落地页和
// 应用之间会重新挂载，芝士答到一半不该跟着丢。换页时芝士照样答完。
import type { AgentFormField } from '@/composables/useAgentPointer'
import type { PanelChange, PanelProposal, PanelResult, PanelStep } from '@/lib/assistantView'
import type { AssistantCited, AssistantMessage, AssistantPage, AssistantProposal } from '@/stores/agentThread'
import type { Citable } from '@/types/citable'

import { computed, onActivated, onDeactivated, onScopeDispose, ref, shallowRef, watch } from 'vue'
import { toast } from 'vuetify-sonner'

import { AgentProposalsApi, ApiError, createRoomTask } from '@/api'
import { AttachmentsApi } from '@/api/attachments'
import { postChatMessage } from '@/api/messages'
import { TeamsApi } from '@/api/teams'
import { t } from '@/i18n'
import { proposalView, stepView, withSubagents } from '@/lib/assistantView'
import { storageGet, storageSet } from '@/lib/browserStorage'
import { panelMessages, resultView, writesView } from '@/lib/draftChangeView'
import { uploadLibraryFile } from '@/lib/libraryApi'
import { myHandle } from '@/me'
import { type AssistantHooks, createAssistant } from '@/stores/agentThread'

/** 这一页在面板上说什么、芝士读什么。 */
export interface AgentContext {
  /** 芝士读到的「当前页面」；没有就只送问题。 */
  page?: AssistantPage | null
  /** 这一页叫什么（「频道「后端」」），写在输入框上方「当前页面」那枚标签上。 */
  label?: string
  /** 这一页在哪个项目里：@ 菜单先列它的东西，也列它资料库里的文件。 */
  project?: { id: string; name: string } | null
  /** 话题空着时问候上面那一行：芝士在这一页读了什么。 */
  intro?: string
  greeting?: string
  starters?: string[]
  placeholder?: string
  hint?: string
  /** 这一页交给芝士的文件是题目的材料（发布题目、改题目）：存成附件，芝士把它挂到题目上。
   *  别的页面交的文件存进这个人自己的资料库。 */
  attachAsMaterials?: boolean
}

/** 面板上那几样随这一页变的东西。 */
export interface AgentPanelView {
  messages: ReturnType<typeof panelMessages>
  steps: PanelStep[]
  changes: PanelChange[]
  results: PanelResult[]
  proposals: PanelProposal[]
}

/** 芝士往里写的一张表单：宿主在它上面画一层，整张表单一个指针（`AgentLayer`）。 */
export interface AgentForm {
  /** 表单的外框（`position: relative`）：那一层画在这里，格子按 `data-agent-field` 找。 */
  host: () => HTMLElement | null
  fields: () => Record<string, AgentFormField>
  /** 芝士正在写的那一格。 */
  writing: () => string | null
  /** 芝士正在改的、编辑器里那一段：先选中原文，再跟着新文字走。 */
  passage?: () => { find: string; replace: string | null } | null
  /** 芝士停在一格前面、还没开始写。 */
  holding?: () => boolean
  /** 芝士写过、人还没再碰的格子，各自是哪次改动。 */
  marks: () => Record<string, number>
  /** 「芝士已填写」旁的撤销。 */
  undoMark: (key: string) => void
}

/** 一页登记的可写面：芝士写进这一页时页面怎么动。都可以不给。 */
export interface AgentSurface {
  form?: AgentForm
  /** 问之前：先把这一页存好，芝士读到的是存下的样子。 */
  prepare?: () => Promise<void>
  on?: AssistantHooks
  /** 面板上每一轮怎么画（改动卡能不能撤销、写着第几道）；不给就照没有表单的页面画。 */
  view?: () => AgentPanelView
  undo?: (change: number, key: string) => void
  undoAll?: (rows: { change: number; key: string }[]) => void
  /** 面板上「跳到这一格」；有表单时不给也行，宿主把那一格滚进视野、描一下。 */
  jump?: (key: string) => void
  openDraft?: (id: number) => void
  openTask?: (id: number) => void
}

interface Entry<T> {
  read: () => T | null
}

function stack<T>() {
  const entries = shallowRef<Entry<T>[]>([])
  const top = computed<T | null>(() => {
    for (let i = entries.value.length - 1; i >= 0; i--) {
      const value = entries.value[i].read()
      if (value) return value
    }
    return null
  })
  function use(read: () => T | null) {
    const entry: Entry<T> = { read }
    const add = () => {
      if (!entries.value.includes(entry)) entries.value = [...entries.value, entry]
    }
    const remove = () => {
      entries.value = entries.value.filter((it) => it !== entry)
    }
    add()
    onActivated(add)
    onDeactivated(remove)
    onScopeDispose(remove)
  }
  return { top, use }
}

const contexts = stack<AgentContext>()
const surfaces = stack<AgentSurface>()

/** 这一页是什么、面板在这里说什么。`read` 随页面状态变；返回 null 是这会儿不声明。 */
export const useAgentContext = contexts.use
/** 芝士写进这一页时页面怎么动。 */
export const useAgentSurface = surfaces.use

export const agentContext = contexts.top
export const agentSurface = surfaces.top

const surfaceHooks = (): AssistantHooks => agentSurface.value?.on ?? {}

// ---- 这一句带什么 -------------------------------------------------------------------

/** 人把「当前页面」关掉了：只管这一页，换一页又带上。 */
export const agentPageOff = ref(false)
const pageKey = computed(() => {
  const page = agentContext.value?.page
  return page ? `${page.kind}:${page.id}` : ''
})
watch(pageKey, () => (agentPageOff.value = false))

/** 人在页面上选中、交给芝士的那段字：跟下一句走，发出就清掉。 */
export const agentSelection = ref<string | null>(null)

/** 用 @ 挑过的东西，按 `<#id>` 里的 id：输入框里的标签和这一句记下的引用都照它写名字。 */
export const agentPicked = shallowRef<Record<string, Citable>>({})

export function pickForAgent(item: Citable) {
  agentPicked.value = { ...agentPicked.value, [item.id]: item }
}

/** 这一页芝士读不读：登记了页面、人又没关掉。 */
export const agentPageOn = computed(() => !!agentContext.value?.page && !agentPageOff.value)

const TOKEN = /<#([\w-]+)>/g

/** 问题里 `<#id>` 引用的、用 @ 挑过的那几样。 */
function citedIn(question: string): AssistantCited[] {
  const ids = [...new Set([...question.matchAll(TOKEN)].map((m) => m[1]))]
  return ids.flatMap((id) => {
    const item = agentPicked.value[id]
    return item ? [{ id, kind: item.kind, title: item.title }] : []
  })
}

/** 全站那一条对话。事件交给此刻登记的可写面。 */
export const agentThread = createAssistant({
  page: () => (agentPageOn.value ? agentContext.value?.page ?? null : null),
  asking: (question) => {
    const selection = agentSelection.value
    const cited = citedIn(question)
    return {
      body: {
        ...(selection ? { selection } : {}),
      },
      shown: {
        ...(agentPageOn.value && agentContext.value?.label ? { askedOn: agentContext.value.label } : {}),
        ...(selection ? { selection } : {}),
        ...(cited.length ? { cited } : {}),
      },
    }
  },
  prepare: async () => {
    await agentSurface.value?.prepare?.()
  },
  on: {
    toolArgs: (call, name, raw) => surfaceHooks().toolArgs?.(call, name, raw),
    toolEnded: (call, name, failed) => surfaceHooks().toolEnded?.(call, name, failed),
    edit: (edit) => surfaceHooks().edit?.(edit),
    published: (done) => surfaceHooks().published?.(done),
  },
})

// ---- 面板开着没有 -------------------------------------------------------------------

/** 面板开着。 */
export const agentOpen = ref(false)
/** 桌面上停在右边（把页面挤窄），还是右下角的浮层：记在这台浏览器里，下次照旧。读不到
 *  存储时停在右边，只是不记。 */
const DOCKED_KEY = 'cheesex.agentDocked'
export const agentDocked = ref(storageGet(DOCKED_KEY) !== 'false')
watch(agentDocked, (value) => void storageSet(DOCKED_KEY, String(value)))
/** 能叫出芝士：登录了。外壳里的宿主写它，入口按它画。 */
export const agentAvailable = ref(false)

export function openAgent() {
  if (!agentAvailable.value) return
  agentOpen.value = true
  agentThread.load().catch(() => undefined)
}

/** 打开面板，并打开这段对话：通知和设置页上的「查看对话」。 */
export function openAgentConversation(id: string) {
  if (!agentAvailable.value) return
  agentOpen.value = true
  agentThread.show(id).catch(() => undefined)
}

export function toggleAgent() {
  if (agentOpen.value) agentOpen.value = false
  else openAgent()
}

// ---- 交给芝士的文件 -----------------------------------------------------------------

export interface AgentFile {
  /** 存成题目材料的那一份的附件编号。 */
  id: number | null
  /** 存进自己资料库的那一份叫什么。 */
  path: string | null
  name: string
  mime: string
  uploading: boolean
}

/** 先传上去，下一个问题带上它们。浅的：传完按那一行本身找回它，行不能被换成代理。 */
export const agentFiles = shallowRef<AgentFile[]>([])

/** 这个人自己的资料库（他名下那个团队的）；问一次就记住。 */
let ownLibrary: Promise<number> | null = null
function myLibrary(): Promise<number> {
  ownLibrary ??= TeamsApi.detailByHandle(myHandle()).then(({ team }) => team.id)
  ownLibrary.catch(() => (ownLibrary = null))
  return ownLibrary
}

async function keep(file: File): Promise<Pick<AgentFile, 'id' | 'path'>> {
  if (agentContext.value?.attachAsMaterials) {
    const done = await AttachmentsApi.upload({ type: 'file', file })
    return { id: done.id, path: null }
  }
  const placed = await uploadLibraryFile({ team: await myLibrary() }, file)
  return { id: null, path: placed.path }
}

export async function attachToAgent(picked: File[]) {
  for (const file of picked) {
    const row: AgentFile = { id: null, path: null, name: file.name, mime: file.type, uploading: true }
    agentFiles.value = [...agentFiles.value, row]
    try {
      const done = await keep(file)
      agentFiles.value = agentFiles.value.map((f) => (f === row ? { ...f, ...done, uploading: false } : f))
    } catch (error) {
      agentFiles.value = agentFiles.value.filter((f) => f !== row)
      // 服务端说了为什么（资料库满了、文件太大）就照它说。
      toast.error(error instanceof ApiError && error.message ? error.message : t('tasks.assistant.files.failed'))
    }
  }
}

export function removeAgentFile(index: number) {
  agentFiles.value = agentFiles.value.filter((_, i) => i !== index)
}

/** 换了一个人：面板收起，对话和待交的文件都不留。 */
export function resetAgent() {
  agentOpen.value = false
  agentFiles.value = []
  agentSelection.value = null
  agentPicked.value = {}
  agentThread.reset()
}

/** 说一句话：带上已经传好的文件。 */
export async function askAgent(text: string) {
  const handed = agentFiles.value
    .filter((f) => !f.uploading)
    .map((f) => (f.path !== null ? { path: f.path, name: f.name } : { id: f.id as number, name: f.name }))
  agentFiles.value = []
  // 芝士还在答：这一句连同附件插进正在答的这一轮（`say`），不另问一个问题。
  const asked = agentThread.busy.value
    ? agentThread.say(text, t('tasks.assistant.failed'), handed)
    : agentThread.ask(text, t('tasks.assistant.failed'), handed)
  // 选中的那段字在问的那一刻已经读走（`asking`），跟的是这一句，不跟下一句。
  agentSelection.value = null
  await asked
}

/** 把页面上选中的一段交给芝士：打开面板，这段字跟着下一句走。 */
export function selectForAgent(text: string) {
  agentSelection.value = text.slice(0, SELECTION_CHARS)
  openAgent()
}

/** 一句最多带这么多选中的字（和后端 `citing.SELECTION_CHARS` 一样）。 */
export const SELECTION_CHARS = 2000

// ---- 面板上画什么 -------------------------------------------------------------------

/** 没有表单的页面：芝士写的改动照样列出来，只是不给撤销。 */
const NO_TERMS = { categories: [], topics: [] }

/** 没有登记可写面时面板怎么画。 */
export function plainView(): AgentPanelView {
  const thread = agentThread
  return {
    messages: panelMessages(thread.messages.value, NO_TERMS, undefined, null, undefined, thread.edits.value),
    steps: withSubagents(thread.steps.value.map(stepView), thread.subagents.value),
    ...writesView(thread.edits.value, NO_TERMS, null, () => false),
    results: thread.published.value.map(resultView),
    proposals: thread.proposals.value.map(proposalView),
  }
}

/** 更早的话题怎么画：读回来的，只给看，不给撤销。 */
export function earlierView() {
  const topics = agentThread.earlier.value
  const editsOf = (messages: AssistantMessage[]) => messages.flatMap((m) => m.edits ?? [])
  // 更早的话题从旧到新排；每一个之后写的（后面的话题、眼下这段、还在写的回答）都比它新。
  const now = [...editsOf(agentThread.messages.value), ...agentThread.edits.value]
  return topics.map((topic, index) => ({
    id: topic.id,
    title: topic.title,
    at: topic.lastActiveAt,
    messages: panelMessages(topic.messages, NO_TERMS, undefined, null, undefined, [
      ...topics.slice(index + 1).flatMap((later) => editsOf(later.messages)),
      ...now,
    ]),
    earlier: topic.earlier === true,
  }))
}

export const agentView = computed<AgentPanelView>(() => agentSurface.value?.view?.() ?? plainView())

// ---- 芝士起草、等人确认的事 ----------------------------------------------------------

/** 正在发出的那一件。 */
export const sendingProposal = ref<number | null>(null)
/** 已经开出、交代还没发进去的任务：再点一次不再开一个。 */
const opened = new Map<number, string>()

function proposalById(id: number): AssistantProposal | undefined {
  const thread = agentThread
  return (
    thread.proposals.value.find((p) => p.id === id) ??
    thread.messages.value.flatMap((m) => m.proposals ?? []).find((p) => p.id === id)
  )
}

/** 人确认了芝士起草的那件事：以他的名义发出去——发消息，或开任务、把交代作为第一条消息
 *  发进去（任务的 AI 队友随之开始做）——再记下发出了。消息带着起草时定下的请求号：重发
 *  只落一条。 */
export async function sendProposal(id: number, edited: { title?: string; text: string }) {
  const p = proposalById(id)
  if (!p || p.state !== 'pending' || sendingProposal.value !== null) return
  sendingProposal.value = id
  try {
    let result: Record<string, string>
    if (p.kind === 'act') {
      let made: string | undefined
      for (const call of p.calls ?? []) {
        const answered = await AgentProposalsApi.run(call, made)
        made = answered && typeof answered.id === 'string' ? answered.id : undefined
      }
      result = { done: 'true' }
    } else if (p.kind === 'task') {
      const task = opened.get(id) ?? (await createRoomTask(p.channel ?? '', edited.title)).id
      opened.set(id, task)
      const posted = await postChatMessage(task, { content: edited.text, request_id: p.request })
      result = { task, topic: task, message: posted.id }
    } else {
      const posted = await postChatMessage(p.topic ?? '', { content: edited.text, request_id: p.request })
      result = { topic: p.topic ?? '', message: posted.id }
    }
    await AgentProposalsApi.settle(id, 'sent', result)
    opened.delete(id)
    agentThread.settleProposal(id, 'sent', result)
  } catch (error) {
    toast.error(error instanceof ApiError ? error.message : t('tasks.assistant.proposal.failed'))
  } finally {
    sendingProposal.value = null
  }
}

/** 撤销芝士改的一项，在没有自己撤销办法的页面上（改通知设置、频道提醒是在哪都能撤的）。 */
export async function undoChange(change: number, key?: string) {
  try {
    await agentThread.undo(change, key)
  } catch (error) {
    toast.error(error instanceof ApiError ? error.message : t('tasks.assistant.changes.undoFailed'))
  }
}

/** 人说不用了。 */
export async function dismissProposal(id: number) {
  try {
    await AgentProposalsApi.settle(id, 'dismissed')
    agentThread.settleProposal(id, 'dismissed')
  } catch (error) {
    toast.error(error instanceof ApiError ? error.message : t('tasks.assistant.proposal.failed'))
  }
}

/** 芝士起草、人开出的那个任务在哪个项目里。 */
export function projectOfOpenedTask(task: string): string | null {
  const all = [...agentThread.proposals.value, ...agentThread.messages.value.flatMap((m) => m.proposals ?? [])]
  return all.find((p) => p.result?.task === task)?.project ?? null
}
