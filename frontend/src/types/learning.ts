// 学习维度 (管理员看板 · 学习): 成员说过的话、卡点、讲解提纲。读的是成员项目里的
// 对话，不是赛题与报名表，所以类型和上面那一组分开。
export type SpaceLearningStudent = {
  handle: string
  name: string
}

export type SpaceLearningKnowledgePoint = {
  categoryId: number
  name: string
}

export type SpaceLearningFilters = {
  students: SpaceLearningStudent[]
  knowledgePoints: SpaceLearningKnowledgePoint[]
  projectCount: number
}

/** 一条能点回原文的成员发言 —— 队列、发言列表、提纲里的摘录都是它。 */
export type SpaceLearningExcerpt = {
  blockId: string
  topicId: string
  projectId: string
  student: string
  studentName: string
  topicTitle: string
  createdAt: number
  quote: string
  /** 知识点是**名字**（没归类就是 null）；筛选那一维传的是分类 id，两者不同。 */
  knowledgePoint?: string | null
}

export type SpaceLearningQuestion = SpaceLearningExcerpt & {
  projectName: string
}

export type SpaceLearningQuestions = {
  questions: SpaceLearningQuestion[]
  total: number
}

export type SpaceLearningStuckPoint = {
  knowledgePoint?: string | null
  studentCount: number
  projectCount: number
  questionCount: number
  latestAt: number
  example: SpaceLearningQuestion
}

export type SpaceLearningQueues = {
  /** 平台没有记录这个信号，所以只有 available: false + reason，没有内容。 */
  reviewFlag: {
    available: boolean
    reason: string
    items: SpaceLearningQuestion[]
  }
  /** 按「多少个成员撞上」排好的共性卡点。 */
  stuckPoints: SpaceLearningStuckPoint[]
}

export type SpaceLearningOutlineSection = {
  knowledgePoint: string
  session: number
  excerpts: SpaceLearningExcerpt[]
}

export type SpaceLearningOutline = {
  title: string
  sections: SpaceLearningOutlineSection[]
  /** 勾中但已经读不到的发言 id。 */
  missing: string[]
}
