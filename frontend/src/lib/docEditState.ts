// 军规 1 (never silently drop content), applied to the doc-editing state machine.
//
// DocPanel juggles three content holders — the visual editor, the source-mode
// draft, and the file on disk — plus a "lossy" flag meaning "the visual editor
// cannot faithfully represent this file". Every place two of those diverge used
// to be resolved by silently dropping one side. These are the pure decisions;
// DocPanel only wires them to refs and DOM so they can be tested for real.

/** What entering 源码模式 should do with the content that exists right now. */
export interface SourceModeEntry {
  /** Content the source (Monaco) editor should open with. */
  draft: string
  /** dirty flag after the switch — true when `draft` differs from the file. */
  dirty: boolean
  /**
   * Unsaved visual-mode edits that were NOT carried into `draft`, kept so the
   * UI can offer them back. `null` when nothing had to be set aside.
   *
   * Non-null happens only on a lossy doc: the source view must show the FILE
   * (showing the degraded serialization would corrupt the escape hatch itself),
   * yet the user's unsaved edits only exist in the degraded serialization.
   * Neither may be dropped, so one is shown and the other is stashed.
   */
  stashed: string | null
}

export function planSourceModeEntry(args: {
  lossy: boolean
  dirty: boolean
  /** Exact markdown currently on disk. */
  rawDoc: string
  /** Markdown the visual editor would write if it saved right now. */
  visualMarkdown: string
}): SourceModeEntry {
  const { lossy, dirty, rawDoc, visualMarkdown } = args
  if (!dirty) return { draft: rawDoc, dirty: false, stashed: null }
  if (!lossy) return { draft: visualMarkdown, dirty: visualMarkdown !== rawDoc, stashed: null }
  return { draft: rawDoc, dirty: false, stashed: visualMarkdown }
}

/**
 * What the header status should say. `paused` is the one that used to lie:
 * a lossy doc pauses visual autosave, so 「编辑中…」 read as "being saved"
 * while the edits were in fact stranded in memory forever.
 */
export type DocSaveStatus = 'loading' | 'saving' | 'paused' | 'dirty' | 'saved' | 'idle'

export function docSaveStatus(args: {
  loading: boolean
  saving: boolean
  dirty: boolean
  lossy: boolean
  sourceMode: boolean
  editable: boolean
  savedAt: number | null
}): DocSaveStatus {
  const { loading, saving, dirty, lossy, sourceMode, editable, savedAt } = args
  if (loading) return 'loading'
  if (saving) return 'saving'
  if (dirty) return autosavePaused({ dirty, lossy, sourceMode, editable }) ? 'paused' : 'dirty'
  if (savedAt) return 'saved'
  return 'idle'
}

/** True when there are unsaved edits that autosave will never pick up. */
export function autosavePaused(args: {
  dirty: boolean
  lossy: boolean
  sourceMode: boolean
  editable: boolean
}): boolean {
  const { dirty, lossy, sourceMode, editable } = args
  if (!dirty) return false
  // Autosave only ever fires for an editable doc. Leaving edit mode saves, so
  // readonly+dirty is normally transient — but when it does persist (a failed
  // save, edits restored from the stash) those edits are stranded, not "being
  // edited".
  if (!editable) return true
  // Source mode edits raw text, so its autosave is never paused.
  return lossy && !sourceMode
}

// ---- The stash ----
// Content set aside by a mode switch or a conflict. A stack, not a slot: a
// second set-aside must not overwrite the first, and restoring must not drop
// whatever was on screen. Both operations only ever move content around.

/** Set `md` aside. No-op when it is already the top (repeat switches). */
export function pushStash(stack: readonly string[], md: string): string[] {
  if (stack.at(-1) === md) return [...stack]
  return [...stack, md]
}

/**
 * Restore the most recent stashed version. `current` (what the editor shows
 * right now) goes back onto the stash unless it is just the file on disk or a
 * duplicate of what we're restoring — so the swap loses nothing either way.
 */
export function popStash(
  stack: readonly string[],
  current: string,
  rawDoc: string
): { restored: string | null; stack: string[] } {
  const restored = stack.at(-1)
  if (restored === undefined) return { restored: null, stack: [] }
  const rest = stack.slice(0, -1)
  const keepCurrent = current !== rawDoc && current !== restored
  return { restored, stack: keepCurrent ? [...rest, current] : rest }
}

/** 「丢弃」: an explicit drop of one stashed version, and only that one. */
export function dropStash(stack: readonly string[]): string[] {
  return stack.slice(0, -1)
}

/**
 * A reload arrived from the server (芝士 wrote the doc). Deciding between:
 * - `ignore`   — disk matches what we already have; nothing to do
 * - `install`  — no local edits at risk, pull it in
 * - `conflict` — both sides moved; surface it, drop neither
 */
export type ExternalUpdatePlan = 'ignore' | 'install' | 'conflict'

export function planExternalUpdate(args: { dirty: boolean; incoming: string; rawDoc: string }): ExternalUpdatePlan {
  const { dirty, incoming, rawDoc } = args
  if (incoming === rawDoc) return 'ignore'
  return dirty ? 'conflict' : 'install'
}
