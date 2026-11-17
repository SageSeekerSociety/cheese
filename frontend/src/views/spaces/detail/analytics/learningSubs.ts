/** 「数据 › 学情分析」的七个子页签，按页面上的顺序。 */
export const LEARNING_SUBS = ['overview', 'points', 'questions', 'words', 'tasks', 'behavior', 'trend'] as const

export type LearningSub = (typeof LEARNING_SUBS)[number]
