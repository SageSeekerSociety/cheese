import { defineStore } from 'pinia'
import { reactive } from 'vue'

import { fetchPresence, type Member } from '@/network/api/threads'

// A site-wide, batched presence cache so ANY avatar (given a user_id) can tell whether
// that user is an agent and show its live cheeselet status. Components call track()/
// untrack() to register interest; the store fetches unknown ids once and then keeps only
// the *agents* refreshing (humans resolve to is_agent=false and stop polling), so a page
// full of human avatars costs a single lookup, not a poll storm.
export const useAgentPresenceStore = defineStore('agentPresence', () => {
  const presence = reactive(new Map<number, Member>())
  const tracked = new Map<number, number>() // user_id -> refcount
  const resolved = new Set<number>() // user_ids fetched at least once
  let timer = 0

  async function tick(): Promise<void> {
    const ids = new Set<number>()
    for (const id of tracked.keys()) {
      // Never-seen ids need a first fetch; known agents need live refresh.
      if (!resolved.has(id) || presence.get(id)?.is_agent) ids.add(id)
    }
    if (!ids.size) return
    try {
      const map = await fetchPresence([...ids])
      for (const id of ids) {
        resolved.add(id)
        presence.set(id, map[id] ?? { user_id: id, nickname: '', avatar_id: null, is_agent: false })
      }
    } catch {
      /* transient — try again next tick */
    }
  }

  function ensurePolling(): void {
    if (timer) return
    void tick()
    timer = window.setInterval(() => void tick(), 1500)
  }

  function track(userId: number): void {
    if (!userId) return
    tracked.set(userId, (tracked.get(userId) ?? 0) + 1)
    if (!resolved.has(userId)) void tick() // resolve new ids promptly, not on the next beat
    ensurePolling()
  }

  function untrack(userId: number): void {
    if (!userId) return
    const n = (tracked.get(userId) ?? 0) - 1
    if (n <= 0) tracked.delete(userId)
    else tracked.set(userId, n)
  }

  function get(userId: number): Member | undefined {
    return presence.get(userId)
  }

  return { presence, track, untrack, get }
})
