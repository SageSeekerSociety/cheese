// 「这个话题的名册刚被改过」。
//
// 同一份话题名册在页面上有不止一份副本：名册抽屉自己拉一份，对话栏（@ 候选、署名）
// 也拉一份，各拉各的。抽屉里加了人，只有抽屉那份知道；对话栏那份要等切一次话题
// 才重拉，于是刚加进来的 AI 队友迟迟 @ 不出来。名册的三种写法（加人、改角色、移出）
// 都在 `api.ts` 里，写成功了就在这里喊一声，手上有副本的人各自重拉。
//
// 后端改名册时不往房间的 socket 推帧，所以别人在另一台机器上改的，这里照样要等下
// 一次拉——这一条只管「这个页面自己改的，这个页面立刻看到」。

type Listener = (topicId: string) => void

const listeners = new Set<Listener>()

export function announceTopicRosterChange(topicId: string): void {
  for (const listener of listeners) listener(topicId)
}

/** 返回退订函数。 */
export function onTopicRosterChange(listener: Listener): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}
