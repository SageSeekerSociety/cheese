export interface AskBlockMeta {
  // ---- cheese_ask 的一道选项题 ---------------------------------------------
  //
  // 提问那一刻写定的那几件（`topics_messages.py`），答完也不动；能答什么，由它们
  // 说了算，不由作答那一刻的请求说了算。
  options?: AskOption[]
  /** 在等谁答：发起那一轮的人。指不到人的时候是 null，不是「谁都行」。 */
  asked?: string | null
  /** 这道题收不收自由输入。作答许可，不是显示开关。 */
  allow_other?: boolean
  /** 界面要不要补「以上都不是」。补出来的那项不写进 `options`。 */
  reject_option?: boolean
  /** 答案日志：末条是当前生效的那一版，前面几条是被更正掉的。 */
  answer_log?: AskAnswerEntry[]

  // ---- 回答那条消息 --------------------------------------------------------
  /** 它答的是哪一道题。 */
  answer_to?: string
  /** 与唤醒那笔投递共用的 event id，两边对得上账。 */
  delivery_event_id?: string
}

/** 一个选项。`text` 是提问方给的那几个字，`explain` 是他补的解释。 */
export interface AskOption {
  text: string
  explain?: string
}

/**
 * 答案日志里的一版。`at` 在**迁移来的历史条目上是 null** —— 旧的 `answered` 从没
 * 记过时刻，写一个出来就是替历史撒谎。
 */
export interface AskAnswerEntry {
  v: number
  kind: 'option' | 'note' | 'reject'
  /** `kind='option'` 时是 `options[].text`，其余为 null —— 不伪造合法选项。 */
  option: string | null
  note: string | null
  by: string
  at: string | null
  /** 作答那一次操作自己的 id。同 id 重试是同一次操作，不是两次作答。 */
  client_op_id: string
}
