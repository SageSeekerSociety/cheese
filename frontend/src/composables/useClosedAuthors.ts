// Which authors off a room's roster closed their accounts (注销). Their old
// messages are signed 「已注销用户」 instead of the handle they left behind;
// someone who merely left the project is not closed and keeps their handle.
import { ref, watch } from 'vue'

import { listClosedAccounts } from '../api/people'

export function useClosedAuthors() {
  const closed = ref<ReadonlySet<string>>(new Set())
  const asked = new Set<string>()

  /** Ask about every author ``authors`` names that has not been asked about yet. */
  function follow(authors: () => string[]) {
    watch(authors, (seen) => {
      const fresh = [...new Set(seen)].filter((a) => !asked.has(a))
      if (!fresh.length) return
      fresh.forEach((a) => asked.add(a))
      listClosedAccounts(fresh)
        .then(({ handles }) => {
          if (handles.length) closed.value = new Set([...closed.value, ...handles])
        })
        // Not answered: ask again when the timeline next changes.
        .catch(() => fresh.forEach((a) => asked.delete(a)))
    })
  }

  return { closed, follow }
}
