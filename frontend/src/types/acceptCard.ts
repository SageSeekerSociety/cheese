// ---- 采纳卡 / 验收 (eval C5/A3) ----

// 这一节原来长在 `@/cx_types` 里，搬出来是因为那个文件顶在 frontend/src 的 1000 行
// 上限上；`@/cx_types` 把它原样转出去，对外一个名字都没变，导入路径照旧写 `@/cx_types`。

export type AcceptStatus =
  | 'pending'
  | 'accepted'
  | 'rejected'
  | 'revoked'
  | 'conflict'
  // 机器闸门 (eval C2): the project's check_command is running / failed.
  | 'pending_gate'
  | 'gate_failed'
  // 闸门没跑成：检查没能在门禁环境里跑起来，对代码没有结论（不是「未通过」）。
  | 'gate_blocked'
  // 两阶段采纳 (2026-08-16 → #718 退役): 历史状态。采纳回到「点一下就是合并」
  // 之后不再有卡进入它，存量卡也已迁回 pending —— 这里留着只为极端残留兜底。
  | 'pr_open'
  | string

// 合并态 (#718): the card's status IS the merge state. Computed server-side
// (backend domain/review/merge_state.py) — the browser never derives it, it
// only puts words and a dot next to what the backend said.
export type MergeStateWord = 'clean' | 'unstable' | 'blocked' | 'behind' | 'dirty' | 'unknown'

// 谁的活 (#718 的表格): who moves next. `human` + no PR = the platform lane,
// where accepting is purely a human judgment and never gated by the state.
export type MergeWho = 'ci' | 'agent' | 'platform' | 'human'

export interface MergeReason {
  kind: string
  // The check names involved, ready for the card face (红了哪个要能看见).
  checks: string[]
  detail: string
}

export interface MergeStateInfo {
  state: MergeStateWord
  who: MergeWho
  // At least one entry server-side; the basis for the verdict.
  reasons: MergeReason[]
  head_sha: string | null
  checked_at: string | null
  since: string | null
}

// 绿了自动合 (#718): only meaningful when the project allows it; armed by a
// reviewer while the card is blocked/behind, merged by the platform when the
// rules are met.
export interface AutoMergeInfo {
  allowed: boolean
  armed_by: string | null
  armed_at: string | null
}

// 托管方的能力位 (ARCH §4.5)。「这个项目接没接 GitHub」不是一个可读的布尔：
// 界面上每一处判断读它要用的那一位。`declaration` 是这个托管方在人点采纳之前就
// 该写在卡上的一句话，GitHub 那一档为空（卡上有提案页链接）。
export interface ForgeInfo {
  // 'unknown' 不是第四种托管方，是「这张卡这会儿读不出自己的托管方」：所有能力位
  // 都是 false，采纳按钮灰着，卡上那句话说的就是这件事。
  kind: 'github_app' | 'forgejo' | 'unknown'
  reports_checks: boolean
  hosts_proposals: boolean
  can_write_remote: boolean
  pushes_to_external_remote: boolean
  identity: 'user' | 'platform'
  declaration: string
}

export interface AcceptCard {
  id: string
  task_id?: string | null
  topic_id: string
  reviewer_handle: string
  focus: string
  // 提交与 PR 规范: the commit subject + body this topic will be squash-merged
  // under. Null on a card filed without them (the platform then falls back to
  // the topic title).
  change_subject: string | null
  change_body: string | null
  status: AcceptStatus
  decided_by: string | null
  // decided_by 自己的芝士替他做的这个决定。
  decided_by_assistant?: boolean
  decided_at: string | null
  note: string
  // 这条 note 是「停住了」(error) 还是「还在走」(info)；空 note 是 null。
  // 后端算好下发（domain/review/notes.py），别在这边按文案开头去猜。
  note_level: 'error' | 'info' | null
  created_at: string
  // 机器闸门: when the check passed + the tail of its output.
  gate_passed_at: string | null
  gate_output: string
  // 主分支保护 (spec §4.4): votes so far / votes needed.
  approvals: string[]
  approvals_required: number
  // 采纳 PR 化 (#188 §5.1): the real GitHub PR this card rides on, when the
  // platform opened one (flag-gated, best-effort).
  pr_number: number | null
  pr_url: string | null
  // 合并态 (#718): what stands between this card and the trunk, and whose move
  // it is. Always present — a platform-lane card carries who="human".
  merge_state: MergeStateInfo
  // 托管方是谁、它能做什么 —— 卡生成的那一刻就带着（后端 domain/review/forge.py）。
  // 这是卡上唯一的一份：别从别的字段推「这个项目接没接 GitHub」。
  forge: ForgeInfo
  auto_merge: AutoMergeInfo
  // 两阶段采纳 (PR迭代式) only: which repo the PR lives in and the commit CI is
  // being queried against.
  pr_repo: string | null
  pr_head_sha: string | null
  pr_merged_at: string | null
  // 这次交付更新的是哪一项产物，以及这张卡自己是它的第几版 (#1085 结论三/五)。
  // 版本号是后端按卡的状态算好的（还没采纳的那一张算的是它采纳之后的号），前端
  // 一个都不推。落地之前递的那些卡没有这一项，所以是 null。
  artifact: { id: string; name: string; version: number } | null
  // 这一版交出去的是什么：一份文件（`filename`，字节在递卡那一刻落了快照）、一个
  // 地址（`url`），或者这次合并本身（`merge`，没有可下载的东西）。
  // revision：这一份是哪一版，采纳时原样带回（交付物可以在待处理时被换掉）。
  deliverable: {
    kind: 'file' | 'link' | 'merge'
    filename: string | null
    url: string | null
    revision?: string | null
  } | null
  completes_task?: boolean // 这次交付是不是任务的最后一步；不是的话采纳后任务接着做下一步
}

// GET /topics/{id}/pr-checks — live CI state of the card's PR (display only).
export interface PrChecks {
  available: boolean
  reason?: string
  pr_number?: number
  pr_url?: string
  state?: string
  mergeable?: boolean | null
  checks?: { name: string; status: string; conclusion: string | null; url?: string }[]
  /** Only for the task's owner, only when the App opened the PR they would have:
   * their GitHub sign-in as it stands — absent, expired, or usable. */
  owner_credit?: 'none' | 'expired' | 'ok' | null
}
