// 话题头的状态标 — product language, not git's (去 PR 化). The branch/merge
// machinery is real underneath, but a normal user shouldn't need to read git to
// know where a topic stands.
//
// It lives here rather than in a component because three of them read it now:
// the topic header above the workspace, ChatPanel's own header (still used by
// 私聊 and 项目本体), and the work panel — which opens on the tab the phase calls
// for. One table, so they can never disagree about what `archived` is called or
// about which of two true things a topic is mostly in.

export interface TopicStateBadge {
  label: string
  /** Class suffix the host styles: `pr-state--open` / `--merged` / `--draft`. */
  cls: string
}

export function topicStateBadge(status?: string | null): TopicStateBadge {
  if (status === 'archived') return { label: '已采纳', cls: 'pr-state--merged' }
  if (status === 'draft') return { label: '草稿', cls: 'pr-state--draft' }
  // 支线只有 open / closed 两个状态，和房间那三个不是一套词。closed 是「这件活
  // 做完了」——不是归档（支线不归档），所以既不能落到 archived，也不能不管它掉进
  // 「进行中」，那会把一条已经收工的支线说成还在跑。
  if (status === 'closed') return { label: '已完成', cls: 'pr-state--merged' }
  return { label: '进行中', cls: 'pr-state--open' }
}

/** 采纳卡处在哪一段. The card owns its own data; everyone else needs one word. */
export type CardPhase = 'gate' | 'pending' | 'delivering' | null

/** Where a topic stands right now — its status, its turn, and its accept card
 * folded into the one answer the header states and the panel opens on. */
export type TopicPhase =
  | 'archived'
  | 'closed'
  | 'draft'
  | 'working'
  | 'delivering'
  | 'reviewing'
  | 'open'

export interface TopicPhaseInput {
  status?: string | null
  /** A turn is in flight in this topic. */
  working?: boolean
  card?: CardPhase
}

export function topicPhase({ status, working, card }: TopicPhaseInput): TopicPhase {
  if (status === 'archived') return 'archived'
  if (status === 'closed') return 'closed'
  if (status === 'draft') return 'draft'
  // The live fact wins over the paperwork: while 芝士 is running, 「待验收」 is
  // describing a card it may be about to supersede, and 「施工中」 is what is
  // actually true of the topic this second.
  if (working) return 'working'
  if (card === 'delivering') return 'delivering'
  if (card) return 'reviewing'
  return 'open'
}

export function topicPhaseBadge(phase: TopicPhase): TopicStateBadge {
  if (phase === 'archived') return { label: '已采纳', cls: 'pr-state--merged' }
  if (phase === 'closed') return { label: '已完成', cls: 'pr-state--merged' }
  if (phase === 'draft') return { label: '草稿', cls: 'pr-state--draft' }
  if (phase === 'working') return { label: '施工中', cls: 'pr-state--working' }
  if (phase === 'delivering') return { label: '交付中', cls: 'pr-state--delivering' }
  if (phase === 'reviewing') return { label: '待验收', cls: 'pr-state--reviewing' }
  return { label: '进行中', cls: 'pr-state--open' }
}

/** The short id a topic is referred to by on screen ("#a1b2c3"). */
export function topicShortId(id?: string | null): string {
  return id ? id.slice(0, 6) : ''
}
