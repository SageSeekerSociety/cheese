// 这条草稿此刻发出去会交给谁：问后端，不在浏览器里另算一遍。
//
// 「交不交给 AI 队友」是后端在落库那一刻按一套规矩定的（@ 了谁、回复的是谁的消息、
// 是不是任务负责人在开着的任务里说话，见后端 `preview_addressing`）。浏览器要是照着
// 抄一份，两份迟早说不一样的话，而输入框上写着的就成了一句假话。所以这里只问：草稿
// 停下来一小会儿就把它交给 `/messages/addressing`，拿回来的是发送时会走的同一条路。
//
// 一次问的是一份「草稿 + 回复对象 + 有没有正文」。地方本身变了（任务关了、又开了）也要
// 重问：`version` 变一下就问。回来晚了的那一次（草稿已经又改了）不用。问不到时留着
// 上一次的答案——宁可晚一点更新，也不让头像在两个答案之间闪一下。
import type { Addressing, AddressingDraft } from '../api/messages'

import { computed, onScopeDispose, shallowRef, watch } from 'vue'

import { previewAddressing } from '../api/messages'

/** 打字停下来多久才问一次。 */
const ADDRESSING_DELAY_MS = 150

export interface AddressingAsk {
  /** 这条消息发在哪段对话里（房间，或房间里的任务、支线）。没有时不问。 */
  place: string | null
  draft: AddressingDraft
}

function keyOf(ask: AddressingAsk): string {
  return JSON.stringify([ask.place, ask.draft.content, ask.draft.reply_to ?? null, ask.draft.with_text !== false])
}

export function useAddressing(options: {
  ask: () => AddressingAsk
  /** 这段对话自己的状态（任务开着还是关了）：变了就重问，草稿没动也一样。 */
  version: () => unknown
}) {
  const answered = shallowRef<{ key: string; addressing: Addressing } | null>(null)
  let timer: ReturnType<typeof setTimeout> | null = null
  let inFlight: AbortController | null = null
  let asked = 0

  async function fetchNow(ask: AddressingAsk) {
    if (!ask.place) return
    const mine = ++asked
    inFlight?.abort()
    const controller = new AbortController()
    inFlight = controller
    try {
      const addressing = await previewAddressing(ask.place, ask.draft, controller.signal)
      // 只认一份说得出「在哪、交给谁」的回答；别的（测试里兜底的假响应之类）当作没问到。
      if (mine === asked && typeof addressing?.place === 'string' && Array.isArray(addressing.people))
        answered.value = { key: keyOf(ask), addressing }
    } catch {
      // 问不到（断线、刚切走）：留着上一次的答案，下一次改草稿或状态变了再问。
    } finally {
      if (inFlight === controller) inFlight = null
    }
  }

  let lastPlace: string | null | undefined
  watch(
    [() => keyOf(options.ask()), options.version],
    () => {
      if (timer) clearTimeout(timer)
      const ask = options.ask()
      // 换了一段对话：上一段的答案不归这里，立刻问，不等打字停下。
      if (ask.place !== lastPlace) {
        lastPlace = ask.place
        answered.value = null
        void fetchNow(ask)
        return
      }
      timer = setTimeout(() => {
        timer = null
        void fetchNow(options.ask())
      }, ADDRESSING_DELAY_MS)
    },
    { immediate: true }
  )

  onScopeDispose(() => {
    if (timer) clearTimeout(timer)
    inFlight?.abort()
  })

  const addressing = computed(() => answered.value?.addressing ?? null)
  /** 手上这份答案说的就是此刻这份草稿（不论地方的状态后来变没变）。 */
  const fresh = computed(() => !!answered.value && answered.value.key === keyOf(options.ask()))
  return { addressing, fresh }
}
