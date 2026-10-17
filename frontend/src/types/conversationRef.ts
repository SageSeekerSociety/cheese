/** 一个 `<#id>` 指着的东西：频道（room）、任务、支线或一份文档，和它所在的频道。
 *  `unreadable`：有这样东西，但读的人无权查看——不给标题。 */
export type ConversationRef = {
  kind: 'room' | 'task' | 'thread' | 'document' | 'unreadable'
  /** 支线没有标题，这里是它挂在下面的那条消息的开头；无权查看的是空串。 */
  title: string
  room_id: string
  project_id: string
}
