// A room document's versions, as the history lists them (api/docHistory.ts).

export interface DocVersion {
  version: number
  content: string
  /** Who made the change: a person, or the AI teammate. */
  actor: string
  /** The person who asked the AI teammate for the change, when one did. */
  requested_by: string | null
  created_at: string
}

export interface DocVersionPage {
  versions: DocVersion[]
  /** Pass as `before` for the page after this one; null when there is none. */
  cursor: number | null
}
