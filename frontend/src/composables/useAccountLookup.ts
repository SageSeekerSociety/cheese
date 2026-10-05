import type { LookedUpUser } from '@/api'

import { ref, watch } from 'vue'

import { lookupUser } from '@/api'

/**
 * Find one person by the exact username or email typed into `query`, as the
 * person types (like adding an external contact in Feishu). Exact only: putting
 * someone into a project or a team is an action, and a list of similar names to
 * pick from is how the wrong person gets added.
 *
 * `failure` turns a failed lookup into the sentence shown under the field; each
 * caller says it in its own words.
 */
export function useAccountLookup(failure: (error: unknown) => string) {
  const query = ref('')
  const found = ref<LookedUpUser | null>(null)
  const lookingUp = ref(false)
  const lookupError = ref<string | null>(null)
  let timer: ReturnType<typeof setTimeout> | null = null
  let seq = 0

  async function run(raw: string) {
    const q = raw.trim()
    if (!q) return
    const mine = ++seq
    lookingUp.value = true
    try {
      const user = await lookupUser(q)
      // Typing outruns the requests: only the last one sent counts, or an older
      // answer arriving late would replace the newer one.
      if (mine !== seq) return
      found.value = user
    } catch (e) {
      if (mine !== seq) return
      lookupError.value = failure(e)
    } finally {
      if (mine === seq) lookingUp.value = false
    }
  }

  watch(query, (raw) => {
    // The person shown belongs to the old text: drop them now, not when the
    // lookup starts, or confirming in the next 350ms acts on someone else. Any
    // answer still in flight is for the old text too.
    found.value = null
    lookupError.value = null
    lookingUp.value = false
    seq++
    if (timer) clearTimeout(timer)
    timer = setTimeout(() => void run(raw), 350)
  })

  function reset() {
    query.value = ''
    found.value = null
    lookupError.value = null
  }

  return { query, found, lookingUp, lookupError, reset }
}
