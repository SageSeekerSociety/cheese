// What a document's mini-app block (`:::app <key>@<version>`) needs from the
// server: the app's versions, one version's code and the shell to run it in,
// and the pictures taken when it was kept.
//
// The asking is wired up by main.ts (`answerDocAppsWith`): this layer touches
// no network, so the block's view — drawn by the editor and by every reader of
// Markdown — does not count as a component that fetches. Where nothing is wired
// up (a test page, the docs site), a block shows its title and sentence only.

export interface DocAppVersionInfo {
  key: string
  version: number
  note: string
  author: string
  requested_by: string | null
  /** Whether the platform's browser ran it before it was kept. */
  checked: boolean
  created_at: string
}

export interface DocApp {
  key: string
  document_id: string
  document_title: string | null
  versions: DocAppVersionInfo[]
}

export interface DocAppVersion extends DocAppVersionInfo {
  code: string
  shell: number
  /** Where the shell is served; null when this deployment runs no mini-app. */
  shell_url: string | null
  /** Height in pixels by width, as the platform's browser measured it. */
  heights: Record<string, number>
  /** The pictures it has, as "<width>-<theme>". */
  shots: string[]
}

export interface DocAppSource {
  app: (key: string) => Promise<DocApp>
  version: (key: string, version: number) => Promise<DocAppVersion>
  /** An object URL of a picture; the caller revokes it. */
  shot: (key: string, version: number, width: number, theme: 'light' | 'dark') => Promise<string>
}

let source: DocAppSource | null = null

/** Who answers for mini-apps: wired to the server when the page starts. */
export function answerDocAppsWith(answer: DocAppSource): void {
  source = answer
}

export function docApps(): DocAppSource | null {
  return source
}

/** Why an app could not be read, as the block says it. */
export type DocAppProblem = 'forbidden' | 'missing' | 'failed'

export function problemOf(error: unknown): DocAppProblem {
  const status = (error as { status?: number } | null)?.status
  if (status === 401 || status === 403) return 'forbidden'
  if (status === 404) return 'missing'
  return 'failed'
}
