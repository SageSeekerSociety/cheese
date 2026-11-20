// 放映这一边和讲者视图窗口之间的那根线（协议见 `lib/slidePresenter.ts`）。
//
// 开一次就留着：同一份稿子再按「讲者视图」只是把那个窗口叫到前面。读者关掉阅读器（换文件、
// 关面板）时告诉讲者视图放映结束了，它停在最后那一页上、不再跟着翻。

import type { FromPresenter, PresenterDeck, ToPresenter } from '@/lib/slidePresenter'

import { onBeforeUnmount, type Ref, ref, watch } from 'vue'

import { newPresenterId, openPresenterWindow, presenterChannel } from '@/lib/slidePresenter'

export type SlidePresenterLinkOptions = {
  /** 递给讲者视图的那一份；还没准备好就是 null。 */
  deck: () => Omit<PresenterDeck, 'page'> | null
  page: Ref<number>
  go: (page: number) => void
}

export function useSlidePresenterLink(options: SlidePresenterLinkOptions) {
  /** 讲者视图开着、而且已经接上了。 */
  const connected = ref(false)
  let channel: BroadcastChannel | null = null
  let popup: Window | null = null

  function post(message: ToPresenter) {
    channel?.postMessage(message)
  }
  function sendDeck() {
    const deck = options.deck()
    // 备注是从响应式的 props 上来的（代理对象），结构化克隆过不去，先拷成普通对象。
    if (deck)
      post({
        type: 'deck',
        deck: { ...deck, notes: JSON.parse(JSON.stringify(deck.notes)), page: options.page.value },
      })
  }
  function hear(event: MessageEvent<FromPresenter>) {
    const message = event.data
    if (message.type === 'hello') {
      connected.value = true
      sendDeck()
    } else if (message.type === 'go') options.go(message.page)
    else if (message.type === 'bye') connected.value = false
  }

  /** 开讲者视图；浏览器拦了弹窗就返回 false。 */
  function open(): boolean {
    if (popup && !popup.closed && channel) {
      popup.focus()
      return true
    }
    channel?.close()
    const id = newPresenterId()
    channel = presenterChannel(id)
    if (!channel) return false
    channel.onmessage = hear
    popup = openPresenterWindow(id)
    if (!popup) {
      channel.close()
      channel = null
      return false
    }
    return true
  }

  watch(options.page, (page) => post({ type: 'page', page }))
  watch(
    () => options.deck()?.bytes,
    () => {
      if (connected.value) sendDeck()
    }
  )
  onBeforeUnmount(() => {
    post({ type: 'end' })
    channel?.close()
    channel = null
  })
  return { open, connected }
}
