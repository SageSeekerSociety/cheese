// 题目页上的芝士（#2285）：这道题上我和芝士的几段对话，和问一个问题。
//
// 一段对话属于开始它的那道题，只有本人看得到。打开面板时接着这道题最近的那一段；
// 「新对话」先不向服务端要一段空的，等第一句话发出去才建 —— 没问过的对话不进列表。
//
// 回答是流式的（server-sent events）：`delta` 是文字，`tool` 是芝士正在查什么，
// `error` 是没答上来的原因，`done` 收尾。回答中途关掉面板不打断服务端：那一轮照样
// 答完、存下、扣费，下次打开还在。
import { computed, ref } from 'vue'

import { request } from '@/api'
import { postEventStream, StreamRefused } from '@/api/eventStream'
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
    const data = await request<AssistantConversation & { messages: AssistantMessage[] }>(
      `/assistant/conversations/${id}`
    )
    current.value = id
    messages.value = data.messages
    notice.value = null
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
    tool.value = null
    const at = new Date().toISOString()
    messages.value = [...messages.value, { role: 'user', text, at }]
    let answer = ''
    // 面板只留服务端存下的东西：被拒的问题不留那一句，没答完的回答不留半截。
    let asked = false
    let failed = false
    try {
      const id = await ensureConversation()
      try {
        const onEvent = (event: string, payload: Record<string, unknown>) => {
          if (event === 'delta' && typeof payload.text === 'string' && payload.text) {
            answer += payload.text
            streaming.value = answer
            tool.value = null
          } else if (event === 'tool') {
            tool.value = typeof payload.name === 'string' ? payload.name : null
          } else if (event === 'error') {
            failed = true
            notice.value = renderNoticeMessage(
              payload.i18n,
              typeof payload.message === 'string' && payload.message ? payload.message : fallback
            )
          }
        }
        await postEventStream(`/assistant/conversations/${id}/ask`, { question: text }, onEvent, {
          onOpen: () => (asked = true),
        })
      } catch (error) {
        if (!(error instanceof StreamRefused)) throw error
        notice.value = refusalText(error.body, error.body.message || fallback)
        creditRefused.value = isCreditRefusal(error.body)
        return
      }
    } catch {
      failed = true
      notice.value = fallback
    } finally {
      if (!asked) messages.value = messages.value.filter((m) => !(m.role === 'user' && m.at === at))
      else if (answer && !failed)
        messages.value = [...messages.value, { role: 'assistant', text: answer, at: new Date().toISOString() }]
      streaming.value = null
      tool.value = null
      await listConversations().catch(() => undefined)
    }
  }

  return {
    conversations,
    current,
    messages,
    streaming,
    tool,
    notice,
    creditRefused,
    busy,
    title,
    load,
    startNew,
    select,
    ask,
  }
}
