import type { LocationQuery, Router } from 'vue-router'

import { computed, type Ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

// 手机上话题里的页签和浏览器历史怎么对应。
//
// 桌面上换页签是 replace：页签是你在看哪儿，不是你去了哪儿，Back 该离开话题。手机上
// 对话是第一格、输入框也在那一格，从别的页签按 Back 离开话题等于丢下了正在进行的对
// 话，所以规则是：
//
// - 离开对话去别的页签，推一条历史；在非对话页签之间换，替换那一条。于是 Back 一下
//   回到对话，再 Back 才离开话题。
// - 从非对话页签点回对话，退回到对话那一条，而不是再推一条：来回切几次，历史不会越
//   攒越长。
// - 直接从链接打开一个非对话页签：先在它下面垫一条对话，Back 照样先回到对话。
//
// 「从这一条退几步是对话」记在这一条历史自己的 state 里（刷新之后还在）。换页签用
// 的 replace 会保留它。

/** 历史 state 里的键：从这一条往回数几步是对话。 */
const DEPTH_KEY = 'roomTabDepth'
const CHAT = 'chat'

function depthOf(router: Router): number {
  const state = router.options.history.state as Record<string, unknown> | undefined
  const depth = state?.[DEPTH_KEY]
  return typeof depth === 'number' && depth > 0 ? depth : 0
}

export function useRoomTabHistory(phone: Ref<boolean>) {
  const router = useRouter()
  const route = useRoute()

  const tab = computed(() => {
    const q = route.query.tab
    return typeof q === 'string' && q ? q : undefined
  })
  /** 手机上地址不带 `?tab=` 就是对话那一格。 */
  const onChat = computed(() => phone.value && (tab.value ?? CHAT) === CHAT)

  function withTab(key: string): LocationQuery {
    return { ...route.query, tab: key }
  }

  /** 回到对话。顶栏的 ← 也是它：身后就是对话时和浏览器的 Back 走同一步。 */
  function toChat() {
    const depth = depthOf(router)
    if (depth > 0) router.go(-depth)
    else void router.replace({ query: { ...withTab(CHAT), card: undefined } })
  }

  /** 工作面板报上来的一次换页签。 */
  function goTab(key: string) {
    if (!phone.value) {
      if (tab.value === key) return
      // replace, not push: a tab is where you are looking, not somewhere you went.
      // Pushing would make Back walk the tabs instead of leaving the topic.
      void router.replace({ query: withTab(key) })
      return
    }
    const current = tab.value ?? CHAT
    if (current === key) return
    if (key === CHAT) toChat()
    else if (current === CHAT) void router.push({ query: withTab(key), state: { [DEPTH_KEY]: 1 } })
    else void router.replace({ query: withTab(key) })
  }

  /**
   * 直接从链接打开了一个非对话页签（身后没有这个话题的对话）：在它下面垫一条对话，
   * 让 Back 先回到对话。刷新不算——那一条的 state 还在。
   */
  async function ensureChatBehind() {
    if (!phone.value || onChat.value || depthOf(router) > 0) return
    const here = { ...route.query }
    await router.replace({ query: { ...here, tab: CHAT } })
    await router.push({ query: here, state: { [DEPTH_KEY]: 1 } })
  }

  return { onChat, goTab, toChat, ensureChatBehind }
}
