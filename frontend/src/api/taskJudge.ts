// 题目测试点的评测（`backend/app/api/routes/tasks/judging.py`）：提交页那块「将要评测的
// 代码」、一版提交的评测结果、老师展开的用例、参与者列表的评测摘要，以及出题人试跑。
import type {
  TaskCodeVersion,
  TaskJudgeRun,
  TaskJudgeSummary,
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

  /** 几版提交各自最近一次评测的摘要（只给能批改的人）。 */
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
}
