// 话题头的状态标 — product language, not git's (去 PR 化). The branch/merge
// machinery is real underneath, but a normal user shouldn't need to read git to
// know where a topic stands.
//
// It lives here rather than in a component because two of them read it: the
// topic header above the workspace and ChatPanel's own header (still used by 私聊
// and 项目本体). One table, so they can never disagree about what `archived` is
// called.

import { t } from '@/i18n'

export interface TopicStateBadge {
  label: string
  /** Class suffix the host styles: `pr-state--open` / `--merged` / `--draft`. */
  cls: string
}

export function topicStateBadge(status?: string | null): TopicStateBadge {
  if (status === 'archived') return { label: t('work.topicState.archived'), cls: 'pr-state--merged' }
  if (status === 'draft') return { label: t('work.topicState.draft'), cls: 'pr-state--draft' }
  // 支线只有 open / closed 两个状态，和房间那三个不是一套词。closed 是「这件活
  // 做完了」——不是归档（支线不归档），所以既不能落到 archived，也不能不管它掉进
  // 「进行中」，那会把一条已经收工的支线说成还在跑。
  if (status === 'closed') return { label: t('work.topicState.done'), cls: 'pr-state--merged' }
  return { label: t('work.topicState.open'), cls: 'pr-state--open' }
}

/** 采纳卡处在哪一段. The card owns its own data; everyone else needs one word. */
export type CardPhase = 'gate' | 'pending' | 'delivering' | null

/** The short id a topic is referred to by on screen ("#a1b2c3"). */
export function topicShortId(id?: string | null): string {
  return id ? id.slice(0, 6) : ''
}

/**
 * 一个频道在屏幕上叫什么。项目本身那个频道（kind = root）按读者的语言叫「综合」，
 * 不照它存着的标题写。
 */
export function topicTitle(topic: { kind?: string | null; title: string }): string {
  if (topic.kind === 'root') return t('navigation.project.general')
  return topic.title
}

/**
 * 一条任务在屏幕上叫什么。还没起名的（`title_source = placeholder`）按读者的语言
 * 叫「新任务」：库里那份占位标题是给 agent 读的中文。
 */
export function taskTitle(task: { title: string; title_source?: string | null }): string {
  return task.title_source === 'placeholder' ? t('work.topic.untitledTask') : task.title
}
