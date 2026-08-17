// 话题头的状态标 — product language, not git's (去 PR 化). The branch/merge
// machinery is real underneath, but a normal user shouldn't need to read git to
// know where a topic stands.
//
// It lives here rather than in a component because two of them render it now:
// the topic header above the workspace, and ChatPanel's own header (still used
// by 私聊 and 项目本体). One table, so the two can never disagree about what
// `archived` is called.

export interface TopicStateBadge {
  label: string
  /** Class suffix the host styles: `pr-state--open` / `--merged` / `--draft`. */
  cls: string
}

export function topicStateBadge(status?: string | null): TopicStateBadge {
  if (status === 'archived') return { label: '已采纳', cls: 'pr-state--merged' }
  if (status === 'draft') return { label: '草稿', cls: 'pr-state--draft' }
  return { label: '进行中', cls: 'pr-state--open' }
}

/** The short id a topic is referred to by on screen ("#a1b2c3"). */
export function topicShortId(id?: string | null): string {
  return id ? id.slice(0, 6) : ''
}
