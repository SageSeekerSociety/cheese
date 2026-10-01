// GET /topics/{id}/preview (spec §9.1): the artifact 芝士 pointed at as the
// topic's current preview. Null when 芝士 hasn't set one.
export interface PreviewInfo {
  /** Content fingerprint for refreshing an updated static preview. */
  version?: string | null
  // File and app previews share an isolated topic content origin.
  kind?: 'file' | 'app'
  path: string
  mime: string | null
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

export interface PreviewSession {
  url: string
  grant: string
  resource_id?: string
  resource?: { kind: 'app' | 'file'; instance?: string; path: string; version?: string }
}

export interface PreviewSelection {
  artifact_id?: string
  path?: string
  version?: string | null
  instance?: string | null
}
