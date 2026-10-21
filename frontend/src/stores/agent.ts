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
import type { PanelChange, PanelResult, PanelStep } from '@/lib/assistantView'
import type { AssistantPage } from '@/stores/agentThread'

import { computed, onActivated, onDeactivated, onScopeDispose, ref, shallowRef, watch } from 'vue'
import { toast } from 'vuetify-sonner'

import { AttachmentsApi } from '@/api/attachments'
import { t } from '@/i18n'
import { stepView } from '@/lib/assistantView'
import { storageGet, storageSet } from '@/lib/browserStorage'
import { panelMessages, resultView, writesView } from '@/lib/draftChangeView'
import { type AssistantHooks, createAssistant } from '@/stores/agentThread'

/** 这一页在面板上说什么、芝士读什么。 */
export interface AgentContext {
  /** 芝士读到的「当前页面」；没有就只送问题。 */
  page?: AssistantPage | null
  /** 话题空着时问候上面那一行：芝士在这一页读了什么。 */
  intro?: string
  greeting?: string
  starters?: string[]
  placeholder?: string
  hint?: string
  /** 这一页能把文件交给芝士。 */
  canAttach?: boolean
}

/** 面板上那几样随这一页变的东西。 */
export interface AgentPanelView {
  messages: ReturnType<typeof panelMessages>
  steps: PanelStep[]
  changes: PanelChange[]
  results: PanelResult[]
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

/** 全站那一条对话。事件交给此刻登记的可写面。 */
export const agentThread = createAssistant({
  page: () => agentContext.value?.page ?? null,
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
/** 这一页能叫出个人芝士：登录了，而且不在项目里（项目里是项目自己的芝士）。外壳里的宿主
 *  按路由写它，入口按它画。 */
export const agentAvailable = ref(false)

export function openAgent() {
  if (!agentAvailable.value) return
  agentOpen.value = true
  agentThread.load().catch(() => undefined)
}

export function toggleAgent() {
  if (agentOpen.value) agentOpen.value = false
  else openAgent()
}

// ---- 交给芝士的文件 -----------------------------------------------------------------

export interface AgentFile {
  id: number | null
  name: string
  mime: string
  uploading: boolean
}

/** 先传上去，下一个问题带上它们。 */
export const agentFiles = ref<AgentFile[]>([])

export async function attachToAgent(picked: File[]) {
  for (const file of picked) {
    const row: AgentFile = { id: null, name: file.name, mime: file.type, uploading: true }
    agentFiles.value = [...agentFiles.value, row]
    try {
      const done = await AttachmentsApi.upload({ type: 'file', file })
      agentFiles.value = agentFiles.value.map((f) => (f === row ? { ...f, id: done.id, uploading: false } : f))
    } catch {
      agentFiles.value = agentFiles.value.filter((f) => f !== row)
      toast.error(t('tasks.assistant.files.failed'))
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
  agentThread.reset()
}

/** 说一句话：带上已经传好的文件。 */
export async function askAgent(text: string) {
  const handed = agentFiles.value.filter((f) => f.id !== null).map((f) => ({ id: f.id as number, name: f.name }))
  agentFiles.value = []
  await agentThread.ask(text, t('tasks.assistant.failed'), handed)
}

// ---- 面板上画什么 -------------------------------------------------------------------

/** 没有表单的页面：芝士写的改动照样列出来，只是不给撤销。 */
const NO_TERMS = { categories: [], topics: [] }

/** 没有登记可写面时面板怎么画。 */
export function plainView(): AgentPanelView {
  const thread = agentThread
  return {
    messages: panelMessages(thread.messages.value, NO_TERMS),
    steps: thread.steps.value.map(stepView),
    ...writesView(thread.edits.value, NO_TERMS, null, () => false),
    results: thread.published.value.map(resultView),
  }
}

/** 更早的话题怎么画：读回来的，只给看，不给撤销。 */
export function earlierView() {
  return agentThread.earlier.value.map((topic) => ({
    id: topic.id,
    title: topic.title,
    at: topic.lastActiveAt,
    messages: panelMessages(topic.messages, NO_TERMS),
  }))
}

export const agentView = computed<AgentPanelView>(() => agentSurface.value?.view?.() ?? plainView())
