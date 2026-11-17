// 出题人试跑测试点那件事的接口：页面（发题页、改题页）提供，表单里的试跑弹窗取用。
// 组件不取数（`.claude/rules/architecture.md`），所以真正去排队、去读结果的那一半由页面
// 用 `provideTestTrial`（`views/tasks/composables/useTaskJudge.ts`）放进来；没有放的地方
// （组件预览站、一次发好几道）表单就不显示「试跑」。
import type { TaskTestSuite, TaskTestTrial, TaskTestTrialFile } from '@/types'
import type { InjectionKey, Ref } from 'vue'

export interface TestTrialRunner {
  trial: Ref<TaskTestTrial | null>
  /** 正在排这一次（还没拿到它的编号）。 */
  starting: Ref<boolean>
  /** 排不上时后端说的那句话。 */
  error: Ref<string>
  run: (suite: TaskTestSuite, files: TaskTestTrialFile[]) => Promise<void>
  stop: () => void
  /** 停下并清掉上一次的结果：换一套测试点打开弹窗时用。 */
  clear: () => void
}

export const TEST_TRIAL: InjectionKey<TestTrialRunner> = Symbol('test-trial')
