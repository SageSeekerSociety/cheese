// Share one in-flight request among concurrent callers that want the same thing.
//
// 同一个话题打开时，两条独立的代码路会几乎同时要同一份数据（节点树：一次给支线
// 徽章，一次给段落评论对齐；最新一页：切话题的预取，和对话面板自己那一条）。第二条
// 发出去只是在占后端的连接、多一次序列化，取回来的还是同一份。让后来的人跟着在飞
// 的那条走。
//
// 「还是同一份」这个前提只对同一次飞行成立：一旦它落地，条目就删掉，所以一次**之后的**
// 显式重读（写完之后、state/doc 帧之后）照样会真的去服务端问一次 —— 这正是我们要的
// 正确性，缓存不能挡住一次真实的刷新。
const inflight = new Map<string, Promise<unknown>>()

export function shareInFlight<T>(key: string, start: () => Promise<T>): Promise<T> {
  const running = inflight.get(key) as Promise<T> | undefined
  if (running) return running
  const started = start().finally(() => {
    // Only clear our own entry: a settled older promise must not delete a newer
    // one that replaced it in the map while we were waiting.
    if (inflight.get(key) === started) inflight.delete(key)
  })
  inflight.set(key, started)
  return started
}
