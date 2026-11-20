// GET /topics/{id}/preview (spec §9.1): the artifact 芝士 pointed at as the
// topic's current preview. Null when 芝士 hasn't set one.
export interface PreviewInfo {
  /** Content fingerprint for refreshing an updated static preview. */
  version?: string | null
  // File, app and mcp-app previews share an isolated topic content origin.
  kind?: 'file' | 'app' | 'mcp-app'
  path: string
  mime: string | null
  /** mcp-app only: which remote MCP server publishes this `ui://` View. */
  server?: string
  // Isolated content URL for files and live apps; null when the app is offline. `tunnel_up` separates "那台机器没有把预览通道拨出来" from
  // "通道在，但应用没在跑" — without it both look like an empty white frame.
  url?: string | null
  tunnel_up?: boolean
  /** Actual listener identity; absent on hosts without fixed-instance support. */
  instance?: string | null
  // Which artifact this is, so a client can tell "芝士 pointed at something new"
  // from "the same preview, re-fetched".
  artifact_id?: string
}

/** 一个跑着的应用最近一次成功加载时留下的静态快照（`/topics/{id}/preview/snapshot`）。 */
export interface PreviewSnapshot {
  html: string
  href: string
  title: string
  /** Unix 秒。 */
  taken_at: number
}

/** 唤醒那几步此刻走到哪了（`/topics/{id}/preview/wake-status`）。 */
export interface PreviewWakeStatus {
  /** 队友在这里的机器：`preparing`、`ready`，没有机器时是 null。 */
  machine: 'preparing' | 'ready' | null
  tunnel_up: boolean
  /** 这段对话有一轮在跑：队友正在重启应用。 */
  agent_working: boolean
  app_up: boolean
}

/** 请队友把应用重新跑起来的结果（`/topics/{id}/preview/wake`）。
 *
 *  `started` 为假时 `reason` 说为什么没起：`up` 应用还答着、`working` 队友正忙（它自己
 *  会看到）、`pending` 刚有人按过、`null` 后端没给理由。 */
export interface PreviewWakeAnswer {
  started: boolean
  reason: 'up' | 'working' | 'pending' | null
}

export interface PreviewSession {
  url: string
  grant: string
  resource_id?: string
  resource?: {
    kind: 'app' | 'file' | 'mcp-app'
    instance?: string
    path?: string
    version?: string
    server?: string
    uri?: string
  }
}

export interface PreviewSelection {
  artifact_id?: string
  path?: string
  version?: string | null
  instance?: string | null
}
