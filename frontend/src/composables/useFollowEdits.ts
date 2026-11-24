// 跟随: the editor goes where the AI teammate is changing the task's files, and shows the change
// as it is written.
//
// The edits come off the room socket (`lib/followEdits`): each frame the page hands over moves
// them on. What the editor draws is `current`, the edit most recently written to, in the task's
// own path: while it is `writing`, the new text grows beside the passage it will replace; once it
// has `landed`, the file has changed on disk, and `landed` is called with that path so the file is
// read again, whether or not anyone follows. A change that landed stays marked for `LANDED_MS`.
//
// Following is the person's choice and ends on its own as soon as they act in the editor
// (`stop`): it never pulls a file out from under someone typing.
import type { WsServerFrame } from '../cx_types'
import type { FollowedEdit } from '../lib/followEdits'
import type { LandedFrame } from '../types/live'

import { computed, onScopeDispose, ref } from 'vue'

import { checkoutPath, editOf } from '../lib/followEdits'

/** How long a landed change stays marked. */
export const LANDED_MS = 4000
/** An edit no frame has moved for this long has been dropped (refused, or its turn ended). */
export const STALE_MS = 60000

export interface CurrentEdit extends FollowedEdit {
  /** The task file it changes. */
  file: string
}

export function useFollowEdits(opts: {
  /** The task's files as listed: a teammate's path is matched against them. */
  listed: () => readonly string[]
  /** A change to `file` has landed: the file on disk is no longer what was read. */
  landed?: (file: string) => void
  now?: () => number
}) {
  const now = opts.now ?? (() => Date.now())
  const edits = ref<Record<string, FollowedEdit & { at: number }>>({})
  const following = ref(false)
  const clock = ref(now())
  let timer: ReturnType<typeof setTimeout> | null = null

  function tick() {
    if (timer) clearTimeout(timer)
    timer = null
    clock.value = now()
    const kept: typeof edits.value = {}
    let soonest = Infinity
    for (const [call, edit] of Object.entries(edits.value)) {
      const ttl = edit.state === 'writing' ? STALE_MS : LANDED_MS
      const left = edit.at + ttl - clock.value
      if (left <= 0) continue
      kept[call] = edit
      soonest = Math.min(soonest, left)
    }
    if (Object.keys(kept).length !== Object.keys(edits.value).length) edits.value = kept
    if (soonest < Infinity) timer = setTimeout(tick, soonest + 1)
  }

  function onLive(agent: string, blocks: Extract<WsServerFrame, { type: 'live' }>['blocks']) {
    let changed = false
    const next = { ...edits.value }
    for (const block of blocks) {
      const edit = editOf(block, agent)
      if (!edit) continue
      const had = next[edit.call]
      // A call that already ran keeps what it was; a late frame does not take it back.
      if (had && had.state !== 'writing') continue
      if (had && had.text === edit.text && had.old === edit.old && had.path === edit.path) continue
      next[edit.call] = { ...edit, at: now() }
      changed = true
    }
    if (changed) {
      edits.value = next
      tick()
    }
  }

  function onLanded(frame: LandedFrame) {
    const next = { ...edits.value }
    const files = new Set<string>()
    for (const { call_id, failed } of frame.calls) {
      const edit = next[call_id]
      if (!edit || edit.state !== 'writing') continue
      next[call_id] = { ...edit, state: failed ? 'failed' : 'landed', at: now() }
      const file = checkoutPath(edit.path, opts.listed())
      if (file && !failed) files.add(file)
    }
    edits.value = next
    tick()
    for (const file of files) opts.landed?.(file)
  }

  /** The page hands over every frame of the room socket; only `live` and `landed` are read. */
  function frame(f: WsServerFrame) {
    if (f.type === 'live' && f.agent) onLive(f.agent, f.blocks)
    else if (f.type === 'landed') onLanded(f)
  }

  /** The edit most recently moved, in a task file; null when there is none. */
  const current = computed<CurrentEdit | null>(() => {
    void clock.value
    let best: (FollowedEdit & { at: number }) | null = null
    for (const edit of Object.values(edits.value)) {
      if (edit.state === 'failed') continue
      if (!best || edit.at >= best.at) best = edit
    }
    if (!best) return null
    const file = checkoutPath(best.path, opts.listed())
    if (!file) return null
    return {
      call: best.call,
      agent: best.agent,
      path: best.path,
      old: best.old,
      oldPending: best.oldPending,
      text: best.text,
      state: best.state,
      file,
    }
  })

  /** The teammate is changing a file now. */
  const active = computed(() => current.value?.state === 'writing')

  function start() {
    following.value = true
  }
  function stop() {
    following.value = false
  }
  /** Another task: nothing written in the last one is anywhere here. */
  function reset() {
    edits.value = {}
    following.value = false
    tick()
  }

  onScopeDispose(() => {
    if (timer) clearTimeout(timer)
  })

  return { following, current, active, frame, start, stop, reset }
}
