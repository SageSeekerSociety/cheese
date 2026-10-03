export interface AskOption {
  /** 1-5 个字，给人看的短标签。 */
  label: string
  /** 一句「选了会怎样」。Codex 管这叫 impact/tradeoff —— 先看后果再点。 */
  description?: string
}

export interface AskQuestion {
  /** 稳定 id，答对得上题（Codex: snake_case）。 */
  id: string
  /** ≤12 字的短标签，用在题与题之间切换的那一条上。 */
  header: string
  /** 一句话，自包含 —— 不靠上面的上下文也看得懂。 */
  question: string
  options: AskOption[]
  /** 「2 小时了」这种。没有就不画岁数那一段。 */
  age?: string
}

export interface AskFlowProps {
  questions: AskQuestion[]
  /** 谁在答。只用来填回执。 */
  answeredBy?: string
  /** 预览站要能逐格摆出某个时刻，所以起点可以给定。产品里用不到这几样。 */
  initialIndex?: number
  initialPicked?: Record<string, number | null>
  initialNotes?: Record<string, string>
  /** 已交但没成功 —— 失败要看得见，不能假装答上了。 */
  initialFailed?: string[]
  /** 已经答过的题。 */
  initialAnswered?: Record<string, { option: string; note: string; by: string; at: string }>
  /** 稍后处理过的题。 */
  initialDeferred?: string[]
  /** 一开始就摆出「有 N 件没答，照样交吗」那一格。 */
  initialConfirm?: boolean
  /**
   * 把回答交出去的那一步。产品里是 `POST /topics/blocks/{id}/answer`，预览里接假服务。
   *
   * **不给就当同步成功** —— 预览站要逐格摆固定时刻，不能每次渲染都去碰网络。
   * 「失败 → 输入还在 → 重试成功」这条链路靠它走通：假服务第一次拒、第二次放行，
   * **组件自己不造失败**。
   */
  submitAnswer?: (payload: { id: string; option: string; note: string }) => Promise<void>
  /**
   * 给了就按这个键把草稿和未交的回答存进 `localStorage`，刷新回来还在。
   *
   * **这是本次的新增目标，不是 Codex 已有的能力** —— 见报告里的来源核对：Codex 的
   * 草稿只在它自己那次进程里（`AnswerState` 是内存态，打断时连已交的答案都不存）。
   * 预览站里给它一个键是为了让「刷新回来还在」这句话当场成立；预览站各格要摆固定
   * 时刻，所以不给键就不存，`initial*` 照旧钉住。
   */
  persistKey?: string
}

/** 一道题自己的答复状态。草稿和「交没交」都记在题上，不记在整批上。 */
export interface QState {
  picked: number | null
  note: string
  /** Codex 管这叫 `answer_committed`：这道题的答复是否被明确交出去过。 */
  committed: boolean
  deferred: boolean
  /** 交了但没成功。 */
  failed: boolean
  submitted: { option: string; note: string; by: string; at: string } | null
  /** 改过答案的话，上一版留在这里 —— 更正不抹掉原来的答案。 */
  was: { option: string; note: string } | null
}

/**
 * 草稿和未交的回答落 `localStorage`，刷新、断线重连回来还在 —— **本次新增目标**。
 *
 * 存的只有「还没交出去」的那部分和已经交过的那部分，不存界面位（当前第几题在
 * `sessionStorage` 里都不必，回来落在第一件还没答的事上更合用）。存不进去（隐私模式、
 * 配额满）就当没这回事，不能因为存不动就不让人答题。
 */
const STORE_PREFIX = 'cheesex.askflow.'

export function saveFlowDraft(persistKey: string, questions: AskQuestion[], state: Record<string, QState>) {
  if (!persistKey) return
  try {
    const out: Record<string, unknown> = {}
    for (const q of questions) {
      const s = state[q.id]
      if (!s) continue
      out[q.id] = {
        picked: s.picked,
        note: s.note,
        deferred: s.deferred,
        submitted: s.submitted,
        was: s.was,
      }
    }
    window.localStorage.setItem(STORE_PREFIX + persistKey, JSON.stringify(out))
  } catch {
    // 存不动就算了，答题不受影响。
  }
}

export function restoreFlowDraft(persistKey: string, questions: AskQuestion[], state: Record<string, QState>) {
  if (!persistKey) return
  try {
    const raw = window.localStorage.getItem(STORE_PREFIX + persistKey)
    if (!raw) return
    const saved = JSON.parse(raw) as Record<string, Partial<QState>>
    for (const q of questions) {
      const s = saved[q.id]
      if (!s) continue
      const cur = state[q.id]
      cur.picked = s.picked ?? cur.picked
      cur.note = s.note ?? cur.note
      cur.deferred = s.deferred ?? cur.deferred
      cur.submitted = s.submitted ?? cur.submitted
      cur.was = s.was ?? cur.was
      cur.committed = Boolean(cur.submitted)
      // 「没交上」是那一刻的事，刷新回来按还没交处理，让人重试而不是永远卡在红字上。
      cur.failed = false
    }
  } catch {
    // 读坏了就当没有。
  }
}
