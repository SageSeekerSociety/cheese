// 题目测试点的评测（`backend/app/api/routes/tasks/judging.py`）：提交页那块「将要评测的
// 代码」、一版提交的评测结果、参与者列表的评测摘要，以及出题人试跑。老师展开隐藏用例不另
// 取：能批改的人读到的评测结果里已经带着每个用例的输入和期望输出。另有学生自测样例、
// 老师改题后重新评测全部最新提交。
import type {
  TaskCodeVersion,
  TaskJudgeRun,
  TaskJudgeStatsView,
  TaskJudgeSummary,
  TaskRejudgeCounts,
  TaskSelfTestView,
  TaskTestSuite,
  TaskTestTrial,
  TaskTestTrialFile,
} from '@/types'

import { axiosRequest } from './axiosHttp'

export namespace TaskJudgeApi {
  /** 这条领题的项目默认分支现在指着的那一版；读不到是 null。 */
  export const codeVersion = (taskId: number, participantId: number) =>
    axiosRequest<{ codeVersion: TaskCodeVersion | null }>(
      { url: `/tasks/${taskId}/participants/${participantId}/code-version`, method: 'GET' },
      true
    )

  /** 一版提交最近一次评测；没评测过是 null。 */
  export const submissionJudge = (taskId: number, participantId: number, submissionId: number) =>
    axiosRequest<{ judge: TaskJudgeRun | null }>(
      { url: `/tasks/${taskId}/participants/${participantId}/submissions/${submissionId}/judge`, method: 'GET' },
      true
    )

  /** 一次最多问几版提交（后端 `MAX_SUMMARY_IDS`）。 */
  export const SUMMARY_BATCH = 200

  /** 几版提交各自最近一次评测的摘要（只给能批改的人），最多 `SUMMARY_BATCH` 版。 */
  export const summaries = (taskId: number, submissionIds: number[]) =>
    axiosRequest<{ summaries: TaskJudgeSummary[] }>(
      { url: `/tasks/${taskId}/judge/summaries`, method: 'GET', params: { submissionIds: submissionIds.join(',') } },
      true
    )

  /** 用参考代码试跑一套测试点（题目不必已经发布）。返回刚排上的这一次。 */
  export const createTrial = (spaceId: number, testSuite: TaskTestSuite, files: TaskTestTrialFile[]) =>
    axiosRequest<{ trial: TaskTestTrial }>(
      { url: `/spaces/${spaceId}/test-trials`, method: 'POST', data: { testSuite, files } },
      true
    )

  /** 读一次试跑的结果。 */
  export const trial = (runId: number) =>
    axiosRequest<{ trial: TaskTestTrial }>({ url: `/tasks/test-trials/${runId}`, method: 'GET' }, true)

  /** 学生自测：拿默认分支现在的代码只跑这一阶段的样例。`stage` 是阶段 id，不分阶段不传。 */
  export const selfTest = (taskId: number, participantId: number, stage?: string | null) =>
    axiosRequest<TaskSelfTestView>(
      {
        url: `/tasks/${taskId}/participants/${participantId}/self-tests`,
        method: 'POST',
        params: stage ? { stage } : undefined,
      },
      true
    )

  /** 这个参与者在这一阶段最近一次自测和今天的次数。 */
  export const latestSelfTest = (taskId: number, participantId: number, stage?: string | null) =>
    axiosRequest<TaskSelfTestView>(
      {
        url: `/tasks/${taskId}/participants/${participantId}/self-tests/latest`,
        method: 'GET',
        params: stage ? { stage } : undefined,
      },
      true
    )

  /** 把每个参与者最新的那一版按现在这一版测试再评一次；不给阶段就是每个挂着测试点的阶段。 */
  export const rejudge = (taskId: number, stage?: string | null) =>
    axiosRequest<TaskRejudgeCounts>(
      { url: `/tasks/${taskId}/judge/rejudge`, method: 'POST', params: stage ? { stage } : undefined },
      true
    )

  /** 「数据」页签的测试点数据（只给能批改的人）：用例通过率、测试分分布、累计额度。 */
  export const stats = (taskId: number, stage?: string | null) =>
    axiosRequest<TaskJudgeStatsView>(
      { url: `/tasks/${taskId}/judge/stats`, method: 'GET', params: stage ? { stage } : undefined },
      true
    )
}
