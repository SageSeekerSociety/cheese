// 这一节原来长在 `@/cx_types` 里，搬出来是因为那个文件顶在 frontend/src 的 1000 行
// 上限上；`@/cx_types` 把它原样转出去，对外一个名字都没变，导入路径照旧写 `@/cx_types`。

// ---- 算力额度 (spec §9.1 机构提供算力 → real quotas) ----

// One credit grant (issued when the project linked an institutional task).
export interface ComputeGrantRow {
  id: string
  project_id: string | null
  source_task_id: number | null
  credits_total: number
  credits_used: number
  created_at: string
}

// Shared team grants plus credits restricted to the requesting project.
export interface ProjectCredits {
  team_id: number | null
  unlimited: boolean
  credits_total: number
  credits_used: number
  credits_remaining: number
  grants: ComputeGrantRow[]
}
