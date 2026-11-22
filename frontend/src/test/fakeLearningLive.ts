// 「这块板的学情分析变了」那条推送的替身：用例自己叫 `pushLearningLive`，像服务端那样
// 叫一次订阅者。一页不开真的 WebSocket（那是一条真连接，网络陷阱会当场报错）。
//
//   vi.mock('@/lib/learningLive', () => import('@/test/fakeLearningLive'))
type Listener = () => void

const listeners = new Map<number, Set<Listener>>()

export function onLearningLive(spaceId: number, listener: Listener): () => void {
  let mine = listeners.get(spaceId)
  if (mine === undefined) {
    mine = new Set()
    listeners.set(spaceId, mine)
  }
  mine.add(listener)
  return () => {
    mine.delete(listener)
  }
}

/** 像服务端那样叫一次：这块板变了。 */
export function pushLearningLive(spaceId: number): void {
  for (const listener of listeners.get(spaceId) ?? []) listener()
}

export function resetLearningLive(): void {
  listeners.clear()
}

export function reconnectDelay(attempt: number, random: () => number = Math.random): number {
  return Math.floor(random() * Math.min(30_000, 1_000 * 2 ** attempt))
}
