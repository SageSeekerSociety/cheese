// What an editor is handed to reach the language servers of a task's checkout
// (`composables/useTaskLanguage` makes it; `components/CodeEditor` takes it by prop).
import type { LspClient } from './client'

/** One language server of the task, shared by every editor of the task on the page. */
export interface LanguageServer {
  /**
   * Needs the server while subscribed. `listener` gets the client each time the server is up
   * and null when it goes; returns the unsubscribe.
   */
  use(listener: (client: LspClient | null) => void): () => void
}

export interface LanguageLink {
  /** Whose checkout the editor's files are in (the task id). */
  readonly scope: string
  /** The server for a Monaco language id, or null when that language has none. */
  serverFor(languageId: string): LanguageServer | null
  /** Show another file of the checkout at a line (go to definition, a reference). */
  open(path: string, line: number): void
}
