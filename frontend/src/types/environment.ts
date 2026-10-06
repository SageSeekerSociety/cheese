// 频道的工作环境没准备好：设置页读回来的那一次失败，和「让芝士看看」的回答。

/** 一个频道最近一次还没人重试的准备失败。 */
export interface EnvironmentFailure {
  attempt: string
  at: string
  stage: 'setup' | 'startup' | null
  exit_code: number | null
  /** 日志的末尾：出错的地方在最后。 */
  log: string
  /** 有几段对话在等它修好。 */
  waiting: number
}

/** 芝士看了这次失败：原因、改法，和改完后的脚本（不用改的是 null）。 */
export interface EnvironmentDiagnosis {
  reason: string
  change: string
  setup_script: string | null
  startup_script: string | null
  sure: boolean
  /** 这次失败时跑的那两段脚本：改法就是相对它们说的。 */
  ran: { setup_script: string; startup_script: string }
}
