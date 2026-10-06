export interface AskBlockMeta {
  // ---- cheese_ask 的一道题 ---------------------------------------------------
  //
  // 题就是一条普通消息，选项是它的快捷回复（`block/questions.py`）。
  options?: AskOption[]
  /** 在等谁答：发起那一轮的人。指不到人的时候是 null，谁回都算。 */
  asked?: string | null
  /** 答过它的每一句话，按先后。 */
  answer_log?: AskAnswerEntry[]
}

/** 一个选项。`text` 是提问方给的那几个字，`explain` 是他补的解释。 */
export interface AskOption {
  text: string
  explain?: string
}

/** 答过这道题的一句话：点了它的一个选项（`option`），或者他自己说的（`note`）。 */
export interface AskAnswerEntry {
  kind: 'option' | 'note' | 'reject'
  option: string | null
  note: string | null
  by: string
  at: string | null
  /** 那句回话本身（房间里的一条消息）。 */
  reply_id?: string
}
