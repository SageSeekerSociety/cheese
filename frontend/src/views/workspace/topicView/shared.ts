// 拆出来的那几个模块共用的东西：这一页在说什么，以及它手里那两块要往回够的组件。
import type { Block, ChatAttachment, WsServerFrame } from '@/cx_types'
import type { DocReviewRequest, OpenedDocument } from '@/lib/docReview'
import type { SubmitPreviewQuestion } from '@/lib/previewQuestion'

/** 这个地址指着谁：一个项目里的一个频道，以及频道下面的一段支线或一个任务。 */
export interface TopicPlace {
  projectId: string
  topicId: string
  taskId?: string
  threadId?: string
  /** 在频道右边审阅的那件任务。 */
  reviewTaskId?: string
}

/** 工作面板往回够的那几处：它自己画自己的，但有几件事只有它做得了。 */
export interface WorkPanelHandle {
  showOverview: () => Promise<void>
  openFile?: (path: string, taskId?: string | null) => void
  siteBlock?: (block: Block) => void
  /** AI 队友正在写、刚跑完的工具调用：编辑器的「跟随」读它。 */
  agentFrame?: (frame: WsServerFrame) => void
  openDocument?: (document: OpenedDocument, review?: DocReviewRequest) => Promise<void>
  openSkill?: (skill: { id: string; title: string }) => Promise<void>
  previewShown?: () => void
  /** 面板此刻在画哪一格。收起再打开要回到它——自动选中的那一格不在地址里，只能问它。 */
  activeTab: () => string
  /** 换到这一格（页头上点了它那一颗）：和读者点它的页签同一件事，地址跟着走。 */
  select?: (key: string) => void
  /** 打开「例行事务」那一格，开出新建一条的那一页。 */
  newRoutine?: () => Promise<void>
  /** 关掉一格（布局区里它那一块的 ✕，或它那一处换了别的）。 */
  closeTab?: (key: string) => void
  /** 编辑器左栏翻到「搜索」，输入框拿到焦点。 */
  searchFiles?: () => void
}

/** 对话栏往回够的那几处。它自己的输入框就是这一页用的那个，这一页只是要个把手。 */
export interface ChatColumnHandle {
  linkDown: boolean
  reloadFeedback: () => void
  say: (content: string, attachments?: ChatAttachment[]) => boolean
  submitQuestion: SubmitPreviewQuestion
}
