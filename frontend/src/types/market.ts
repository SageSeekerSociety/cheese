// ---- 资源池市场 (design v3: AI 池 + 算力池) ----

// 这一节原来长在 `@/cx_types` 里，搬出来是因为那个文件顶在 frontend/src 的 1000 行
// 上限上；`@/cx_types` 把它原样转出去，对外一个名字都没变，导入路径照旧写 `@/cx_types`。

// A pool listing in the 市场 catalog / a project's settings selector.
export interface PoolListing {
  kind: 'ai' | 'compute'
  id: string
  label: string
  tier: string
  price: string
  description: string
  available: boolean
  default: boolean
}

// GET /api/market/pools
export interface MarketPools {
  ai: PoolListing[]
  compute: PoolListing[]
}

// ---- 节点看板 (spec §9.1: where turns physically run) ----

// One compute node this deployment runs, with liveness.
export interface MarketNode {
  id: string
  label: string
  // Which sandbox spec this node IS — the same id the 市场 catalogue lists.
  kind: 'standard' | 'vm' | 'team_devices'
  online: boolean
  // Whether an unconfigured topic lands on this node.
  current: boolean
  detail: string
  description: string
}

// GET /api/market/nodes
export interface MarketNodes {
  nodes: MarketNode[]
  active_turns_total: number
  current_provider: string
}

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
