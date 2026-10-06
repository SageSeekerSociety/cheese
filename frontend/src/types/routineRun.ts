// 例行任务的一次执行是 AI 队友在频道主线上的一条消息，执行本身在它的支线里。
// `GET /topics/{频道}/blocks` 在那一条上带着这一行（后端 `routine/reads.py`）。
// 写在这里而不是 cx_types 里那个接口上：那个文件已经超长，只许变短。

export interface RoutineRunLine {
  routine_id: string
  run_id: string
  title: string
  /** queued / running：还在跑；之后是这次执行自己的结果。 */
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'skipped'
  /** 没成功时平台说的原因。 */
  reason: string
  outputs: string[]
  started_at: string | null
  finished_at: string | null
  /** 执行所在的支线。 */
  thread_id: string | null
}

declare module '../cx_types' {
  interface Block {
    routine_run?: RoutineRunLine | null
  }
}
