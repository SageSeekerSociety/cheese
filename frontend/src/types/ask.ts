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
}

/** 答过这道题的一句话：点了它的一个选项（`option`），或者他自己说的（`note`）；
 *  `skipped` 是被问的人跳过了它，不带选项也不带话，`reply_id` 为 null。
 *  `withdrawn` / `expired` 是提问方收起题的那一条（撤回 / 过了时效）：算题的状态，
 *  不算回答，`by` 是提问方。 */
export interface AskAnswerEntry {
  kind: 'option' | 'note' | 'reject' | 'skipped' | 'withdrawn' | 'expired'
  option: string | null
  note: string | null
  by: string
  at: string | null
  /** 那句回话本身（房间里的一条消息）。 */
  reply_id?: string | null
}
