/** 一个 `<#id>` 指着的东西：频道（room）、任务或支线，和它所在的频道。 */
export type ConversationRef = {
  kind: 'room' | 'task' | 'thread'
  /** 支线没有标题，这里是它挂在下面的那条消息的开头。 */
  title: string
  room_id: string
  project_id: string
}
