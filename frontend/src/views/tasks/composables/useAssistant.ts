// 题目页上的芝士（#2285）：这道题上我和芝士的几段对话，和问一个问题。
//
// 一段对话属于开始它的那道题，只有本人看得到。打开面板时接着这道题最近的那一段；
// 「新对话」先不向服务端要一段空的，等第一句话发出去才建 —— 没问过的对话不进列表。
//
// 回答是流式的（server-sent events）：`queued` 是在等会话机空出来，`delta` 是文字，
// `tool` 是芝士正在查什么，`error` 是没答上来的原因，`done` 收尾（`stopped`：人按了
// 停止，写到哪儿算哪儿）。回答在服务端答到底，跟这条连接无关：关掉面板、刷新页面、
// 后端重启，那一轮照样答完、存下、扣费；连接断了就从断的地方接着读，打开一段还在答的
// 对话也接着看。要它停只有「停止」。
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

export interface AssistantMessage {
  role: 'user' | 'assistant'
  text: string
  /** 回答被人停下了；`text` 是停下时已经写出的部分。 */
  stopped?: boolean
  at: string
}

export function useAssistant(taskId: () => number) {
  const conversations = ref<AssistantConversation[]>([])
  const current = ref<string | null>(null)
  const messages = ref<AssistantMessage[]>([])
  /** 正在流进来的那一段回答；没有在答时是 null。 */
  const streaming = ref<string | null>(null)
  /** 芝士此刻在用的工具名（`cheese_docs_search` 之类），面板把它说成人话。 */
  const tool = ref<string | null>(null)
  /** 在等会话机空出来，还没开始答。 */
  const queued = ref(false)
  /** 没答上来、额度用完之类要告诉人的那一句。 */
  const notice = ref<string | null>(null)
  /** 这次被拒是因为额度不够：面板在提示旁给「查看用量」。 */
  const creditRefused = ref(false)
  const loaded = ref(false)

  const busy = computed(() => streaming.value !== null)
  const title = computed(() => conversations.value.find((c) => c.id === current.value)?.title ?? '')

  async function listConversations() {
    const data = await request<{ conversations: AssistantConversation[] }>(`/assistant/tasks/${taskId()}/conversations`)
    conversations.value = data.conversations
  }

  async function open(id: string) {
    const data = await request<AssistantConversation & { messages: AssistantMessage[]; answering?: string | null }>(
      `/assistant/conversations/${id}`
    )
    current.value = id
    messages.value = data.messages
    notice.value = null
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

  /** 打开面板：接着这道题最近的那一段；一段都没有就是一段新的。 */
  async function load() {
    if (loaded.value) return
    await listConversations()
    loaded.value = true
    const latest = conversations.value[0]
    if (latest) await open(latest.id)
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
    const data = await request<AssistantConversation>(`/assistant/tasks/${taskId()}/conversations`, {
      method: 'POST',
    })
    conversations.value = [data, ...conversations.value]
    current.value = data.id
    return data.id
  }

  async function ask(question: string, fallback: string) {
    const text = question.trim()
    if (!text || busy.value) return
    notice.value = null
    creditRefused.value = false
    streaming.value = ''
    const at = new Date().toISOString()
    messages.value = [...messages.value, { role: 'user', text, at }]
    // 面板只留服务端存下的东西：被拒的问题不留那一句。
    let asked = false
    try {
      const id = await ensureConversation()
      await follow(
        id,
        (onEvent) =>
          postEventStream(`/assistant/conversations/${id}/ask`, { question: text }, onEvent, {
            onOpen: () => (asked = true),
          }),
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
    let answer = ''
    let failed = false
    let stopped = false
    let done = false
    const onEvent: OnEvent = (event, payload) => {
      if (event === 'queued') {
        queued.value = true
      } else if (event === 'delta' && typeof payload.text === 'string' && payload.text) {
        // 接着读时可能再听到一遍已经有的字：`at` 说这段从第几个字起。
        const from = typeof payload.at === 'number' ? payload.at : answer.length
        answer = answer.slice(0, from) + payload.text
        streaming.value = answer
        tool.value = null
        queued.value = false
      } else if (event === 'tool') {
        tool.value = typeof payload.name === 'string' ? payload.name : null
        queued.value = false
      } else if (event === 'done') {
        done = true
        stopped = payload.stopped === true
      } else if (event === 'error') {
        failed = true
        notice.value = renderNoticeMessage(
          payload.i18n,
          typeof payload.message === 'string' && payload.message ? payload.message : fallback
        )
      }
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
      if (current.value === conversation && ((answer && !failed && done) || stopped))
        messages.value = [
          ...messages.value,
          { role: 'assistant', text: answer.trim(), stopped, at: new Date().toISOString() },
        ]
      if (current.value === conversation) {
        streaming.value = null
        tool.value = null
        queued.value = false
      }
    }
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
    queued,
    notice,
    creditRefused,
    busy,
    title,
    load,
    startNew,
    select,
    ask,
    stop,
  }
}
