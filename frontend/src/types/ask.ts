export interface AskBlockMeta {
  // ---- cheese_ask 的一道题 ---------------------------------------------------
  //
  // 题就是一条普通消息，选项是它的快捷回复（`block/questions.py`）。
  options?: AskOption[]
  /** 问了谁：芝士点名的人（问所有人时是频道里当时的每个人）。空是没点名，谁回都算。 */
  asked?: string[]
  /** 问的是频道里所有人。 */
  asked_all?: boolean
  /** 答过它的每一句话，按先后。 */
  answer_log?: AskAnswerEntry[]
}

/** 一个选项。`text` 是提问方给的那几个字，`explain` 是他补的解释。 */
export interface AskOption {
  text: string
  explain?: string
  /** 平台给的按钮（`agent.ask.ask_to_decide`）：点了不是回一句话，而是做卡上、任务上
   *  那个动作。 */
  action?: AskAction
}

/** 一道题上替交付做决定的按钮：`accept` 调卡的采纳（带着问的时候卡上那一版），
 *  `close` 调任务的关闭。谁能点由那两个接口自己判断，和卡上、任务上的按钮一样。 */
export type AskAction =
  | {
      kind: 'accept'
      card: string
      completes_task: boolean
      head_sha: string | null
      deliverable_revision: string | null
    }
  | { kind: 'close'; card: string; task: string }

/** 答过这道题的一句话：点了它的一个选项（`option`），或者他自己说的（`note`）；
 *  `skipped` 是被问的人跳过了它，不带选项也不带话，`reply_id` 为 null。
 *  `withdrawn` / `expired` 是提问方收起题的那一条（撤回 / 过了时效）：算题的状态，
 *  不算回答，`by` 是提问方。`decided` 是题的按钮要决定的那份交付已经有了结果
 *  （`block.questions.settle_decision`）：`option` 是它相当于哪个按钮，没有就是 null。 */
export interface AskAnswerEntry {
  kind: 'option' | 'note' | 'reject' | 'skipped' | 'withdrawn' | 'expired' | 'decided'
  option: string | null
  note: string | null
  by: string
  at: string | null
  /** 那句回话本身（房间里的一条消息）。 */
  reply_id?: string | null
}
