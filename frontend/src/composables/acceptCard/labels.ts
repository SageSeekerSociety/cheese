/** 卡上「采纳」按钮写哪几个字：这次交付是不是任务的最后一步决定它。卡上的按钮和对话里
 *  请人决定这份交付的那道题（`AskQuickReplies`）用同一个，同一个动作只有一个名字。 */
export function acceptLabelKey(completesTask: boolean): string {
  return completesTask ? 'work.room.accept.acceptAndComplete' : 'work.room.accept.acceptAction'
}
