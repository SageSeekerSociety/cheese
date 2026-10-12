// 任务的卡片（`GET|PATCH /topics/{task}/map-card`）：这件事是干什么的、到哪了、在等谁、
// 读的人可能不懂的词。项目地图上每个任务一张，任务页最上面先画它。它从哪句话来不在这里，
// 在 `GET /topics/{task}/related` 的 `source`。
export interface MapCardWaiting {
  /** 项目成员的 handle。 */
  handle: string
  /** 要他做什么。 */
  ask: string
}

export interface MapCardTerm {
  term: string
  meaning: string
}

export interface MapCard {
  what: string
  progress: string
  waiting_on: MapCardWaiting[]
  terms: MapCardTerm[]
  updated_by: string
  updated_at: string
}

export interface MapCardView {
  /** 还没人写过时是 null。 */
  card: MapCard | null
  can_edit: boolean
}

/** 一次修改：给了的栏整栏换掉，没给的不动。 */
export type MapCardChange = Partial<Pick<MapCard, 'what' | 'progress' | 'waiting_on' | 'terms'>>
