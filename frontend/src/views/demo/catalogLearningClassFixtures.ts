/** 「数据 › 学情分析」（全班整体）的样例数据：一门数据库课，38 人。例句只标代号。 */
import type {
  LearningClassBehavior,
  LearningClassExcerpts,
  LearningClassOverview,
  LearningClassPointDetail,
  LearningClassPoints,
  LearningClassQuestions,
  LearningClassTasks,
  LearningClassTrend,
  LearningClassWords,
} from '@/types/apiSpaces'

const at = Date.parse('2026-11-16T06:00:00Z')

export const LEARNING_CLASS_OVERVIEW: LearningClassOverview = {
  mastery: {
    members: 23,
    reported: 6,
    points: 38,
    levels: { mastered: 41, shaky: 22, weak: 13 },
    judged: 76,
    peopleJudged: 18,
  },
  enabled: true,
  minPeople: 3,
  updatedAt: at,
  members: 38,
  completion: { rate: 0.78, submitted: 148, expected: 190 },
  messagesThisWeek: 1284,
  weakPoints: 2,
  tasks: [
    { taskId: 1, name: 'T1 建表与约束', analysed: true, notStarted: 0, inProgress: 2, submitted: 4, passed: 32 },
    { taskId: 3, name: 'T3 子查询与聚合', analysed: true, notStarted: 5, inProgress: 9, submitted: 9, passed: 15 },
    {
      taskId: 5,
      name: 'T5 事务实验',
      analysed: false,
      notStarted: null,
      inProgress: null,
      submitted: null,
      passed: null,
    },
  ],
  focus: [
    { knowledgePointId: 2, name: '相关子查询', stuck: 22, involved: 33 },
    { knowledgePointId: 4, name: '范式 3NF / BCNF', stuck: 21, involved: 26 },
    { knowledgePointId: 3, name: 'GROUP BY 与 HAVING', stuck: 18, involved: 30 },
  ],
}

export const LEARNING_CLASS_OFF: LearningClassOverview = {
  ...LEARNING_CLASS_OVERVIEW,
  enabled: false,
  members: 0,
  tasks: [],
  focus: [],
}

export const LEARNING_CLASS_POINTS: LearningClassPoints = {
  enabled: true,
  minPeople: 3,
  members: 38,
  askedRatio: 0.6,
  stuckRatio: 0.55,
  askedThreshold: 23,
  points: [
    {
      id: 1,
      name: '多表连接 JOIN',
      analysed: true,
      asked: 34,
      involved: 34,
      stuck: 8,
      stuckRate: 0.235,
      quadrant: 'focus',
    },
    {
      id: 2,
      name: '相关子查询',
      analysed: true,
      asked: 33,
      involved: 33,
      stuck: 22,
      stuckRate: 0.667,
      quadrant: 'both',
    },
    {
      id: 5,
      name: '事务隔离级别',
      analysed: true,
      asked: 12,
      involved: 20,
      stuck: 16,
      stuckRate: 0.8,
      quadrant: 'hard',
    },
    {
      id: 6,
      name: '视图',
      analysed: false,
      asked: null,
      involved: null,
      stuck: null,
      stuckRate: null,
      quadrant: null,
    },
  ],
}

export const LEARNING_CLASS_POINT: LearningClassPointDetail = {
  enabled: true,
  minPeople: 3,
  knowledgePoint: { id: 2, name: '相关子查询' },
  analysed: true,
  members: 38,
  involved: 33,
  stuck: 22,
  newThisWeek: 4,
  tasks: ['T2 多表查询', 'T3 子查询与聚合'],
  mastery: { mastered: 11, shaky: 12, weak: 10, untouched: 5 },
  misconceptions: [
    {
      id: 11,
      label: '内层不知道怎样引用外层当前行',
      people: 13,
      examples: [
        { code: 'A7F3', quote: '子查询里怎么引用外层那一行我一直没搞懂。', taskName: 'T3 子查询与聚合' },
        { code: '5B1D', quote: '为什么里面能直接写 t.dept，t 不是外面的吗？', taskName: 'T3 子查询与聚合' },
      ],
    },
    { id: 12, label: '把 WHERE 和 HAVING 混用', people: 8, examples: [] },
    {
      id: 13,
      label: '以为子查询一定比 JOIN 慢',
      people: 2,
      examples: [
        {
          code: '6C2E',
          quote: '听说子查询性能差，是不是都该改成 JOIN？',
          taskName: 'T3 子查询与聚合',
        },
      ],
    },
  ],
  advice: {
    text: '用一张「外层逐行扫描、内层每次重算」的示意，配同一题的相关子查询和派生表两种写法对照，5 分钟左右。',
    generatedAt: at,
  },
  practice: { practiced: 9, improved: 5 },
  lastPush: { at, recipients: 22 },
}

export const LEARNING_CLASS_QUESTIONS: LearningClassQuestions = {
  enabled: true,
  minPeople: 3,
  totalQuestions: 286,
  totalClusters: 37,
  hiddenClusters: 30,
  clusters: [
    {
      id: 21,
      label: '相关子查询里怎么引用外层查询的列？',
      knowledgePointId: 2,
      knowledgePointName: '相关子查询',
      people: 19,
      asks: 41,
      trend: 'up',
      example: { code: 'A7F3', quote: '子查询里怎么引用外层那一行我一直没搞懂。' },
    },
    {
      id: 22,
      label: '不可重复读和幻读有什么区别？',
      knowledgePointId: 5,
      knowledgePointName: '事务隔离级别',
      people: 9,
      asks: 14,
      trend: 'new',
      example: { code: 'C2E9', quote: '幻读是不是就是读到了别人没提交的数据？' },
    },
  ],
}

export const LEARNING_CLASS_WORDS: LearningClassWords = {
  enabled: true,
  minPeople: 3,
  windowDays: 7,
  words: [
    { word: '子查询', count: 212, people: 31, isNew: false, rising: false },
    { word: 'GROUP', count: 187, people: 29, isNew: false, rising: true },
    { word: '幻读', count: 38, people: 9, isNew: true, rising: false },
  ],
  errors: [{ line: 'ERROR 1111 (HY000): Invalid use of group function', count: 23, people: 14 }],
}

export const LEARNING_CLASS_TASKS: LearningClassTasks = {
  enabled: true,
  minPeople: 3,
  tasks: [
    {
      taskId: 3,
      name: 'T3 子查询与聚合',
      analysed: true,
      claims: 33,
      completionRate: 0.64,
      avgTurns: 17,
      avgHours: 2.9,
      stuckRate: 0.58,
      answerSeekers: 6,
    },
    {
      taskId: 5,
      name: 'T5 事务实验',
      analysed: false,
      claims: null,
      completionRate: null,
      avgTurns: null,
      avgHours: null,
      stuckRate: null,
      answerSeekers: null,
    },
  ],
}

export const LEARNING_CLASS_BEHAVIOR: LearningClassBehavior = {
  enabled: true,
  minPeople: 3,
  analysed: true,
  totalQuestions: 286,
  kinds: [
    { kind: 'concept', count: 97, share: 0.34 },
    { kind: 'error', count: 80, share: 0.28 },
    { kind: 'answer', count: 52, share: 0.18 },
    { kind: 'verify', count: 40, share: 0.14 },
    { kind: 'other', count: 17, share: 0.06 },
  ],
  slots: ['0-8', '8-12', '12-18', '18-22', '22-24'],
  heat: [
    [null, 3, 4, 6, null],
    [null, null, 5, 7, 3],
    [null, 4, 6, 8, 3],
    [null, 3, 4, 6, null],
    [null, null, 3, 5, 4],
    [null, null, 3, 6, 5],
    [3, null, 5, 12, 14],
  ],
  dependency: { low: 21, mid: 11, high: 6 },
}

export const LEARNING_CLASS_TREND: LearningClassTrend = {
  enabled: true,
  minPeople: 3,
  weeks: ['2026-10-05', '2026-10-12', '2026-10-19', '2026-10-26', '2026-11-02', '2026-11-09'],
  series: [
    { knowledgePointId: 2, name: '相关子查询', values: [null, 6, 14, 19, 22, 22] },
    { knowledgePointId: 3, name: 'GROUP BY 与 HAVING', values: [null, 9, 20, 24, 19, 18] },
  ],
}

export const LEARNING_CLASS_EXCERPTS: LearningClassExcerpts = {
  enabled: true,
  minPeople: 3,
  analysed: true,
  title: '相关子查询里怎么引用外层查询的列？',
  excerpts: [
    {
      code: 'A7F3',
      taskName: 'T3 子查询与聚合',
      at,
      messages: [
        {
          role: 'member',
          code: 'A7F3',
          text: '老师好，我是[姓名]，学号 [学号]。查每个系里工资高于本系平均工资的老师，我这样写为什么报错？',
          at,
        },
        {
          role: 'agent',
          code: null,
          text: '聚合函数不能直接写在 WHERE 里，而且「本系平均」要随每一行老师的系变化。',
          at,
        },
      ],
      source: null,
    },
  ],
}
