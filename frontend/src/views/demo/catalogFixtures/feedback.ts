/**
 * 反馈列表里的一行，和学习摘录那两条。
 *
 * 从 `../catalogFixtures.ts` 拆出来的一册：一行吃到的就是后端
 * `schemas.FeedbackCard` 本身（页面不再转第二种形状），所以这一册照那个形状造。
 * 条目在 `../catalog/account.ts` 与 `../catalog/analytics.ts`。
 */
import type { FeedbackCard } from '@/cx_types'
import type { SpaceLearningExcerpt } from '@/types/apiSpaces'

// 一行吃到的就是后端 `schemas.FeedbackCard` 本身（页面不再转第二种形状），所以这里
// 照那个形状造，只有 `created_at` 是「昨天」，好让底行那句相对时间读起来正常。

/** 一行反馈。默认这一条是公开、刚收录、还没人支持的那一种；其余几格只改差的那几项。 */
function feedbackRow(over: Partial<FeedbackCard> = {}): FeedbackCard {
  return {
    id: 'fb-1024',
    display_id: 'FB-1024',
    kind: 'bug',
    title: '导出一个月的数据要等四十秒',
    summary: '每次导出都要重跑一遍全量聚合，数据一多就卡在那儿转。',
    status: 'received',
    priority: 'normal',
    visibility: 'public',
    security: false,
    author_handle: 'alice',
    author_is_agent: false,
    author_avatar_id: null,
    submitted_by_handle: null,
    assignee_handle: null,
    tags: [],
    supports: 0,
    comments: 0,
    supported: false,
    last_activity_at: null,
    created_at: '2026-09-28T09:12:00Z',
    ...over,
  }
}

export const FEEDBACK_ROWS: Record<string, FeedbackCard> = {
  plain: feedbackRow(),
  /** 支持过了：图标实心、数字变色、底色起来（三个信号一起变，不只换颜色）。 */
  supported: feedbackRow({ supported: true, supports: 12, comments: 4, tags: ['导出', '性能'] }),
  /** 摘要和标题是同一句话：这一行不画摘要（组件文件头那段）。 */
  sameLine: feedbackRow({ summary: '  导出一个月的数据要等四十秒  ' }),
  /** 长标题 + 长摘要：标题一行就截，摘要两行封顶。 */
  long: feedbackRow({
    title: '导出一个月的数据要等四十秒，而且导出到一半切到别的页面就全没了',
    summary:
      '每次导出都要重跑一遍全量聚合，数据一多就卡在那儿转；退出去再回来得从头开始，中间那一半文件也没有落下来。试过换浏览器，一样。',
  }),
  /** 不能公开的条目：没有支持按钮 —— 它不该让人知道它存在（行政标的也一样）。 */
  private: feedbackRow({ visibility: 'private', title: '后台有个接口会把手机号回显出来' }),
  /** 芝士提的：来源那一颗写「AI 队友」，提交人是别人。 */
  agent: feedbackRow({
    kind: 'suggestion',
    author_handle: 'cheese',
    author_is_agent: true,
    submitted_by_handle: 'alice',
    title: '反馈列表里那一行可以再挤进一条标签',
  }),
  /** 办完了（已上线）：支持按钮变灰不可点，提示语换成「这条已经处理完了」。 */
  closed: feedbackRow({ status: 'deployed', supported: true, supports: 12, comments: 4 }),
}

export const EXCERPTS: SpaceLearningExcerpt[] = [
  {
    blockId: 'b-1',
    topicId: 't-1',
    projectId: 'p1',
    student: 'alice',
    studentName: '爱丽丝',
    topicTitle: '第 3 题：为什么天空是蓝的',
    createdAt: Date.parse('2026-09-28T09:12:00Z'),
    quote: '因为瑞利散射，短波长的光被散射得更多。',
    knowledgePoint: '瑞利散射',
  },
  {
    blockId: 'b-2',
    topicId: 't-9',
    projectId: 'p1',
    student: 'bob',
    studentName: '鲍勃',
    topicTitle: '第 5 题：这道题我错在哪',
    createdAt: Date.parse('2026-09-28T10:31:00Z'),
    quote:
      '我把第三问当成了求最大值，其实题目问的是「至少需要多少」——重新读了一遍才发现是下界，不是极值，所以整段推导都用错了方向，后面几问跟着一起错。',
    knowledgePoint: null,
  },
]
