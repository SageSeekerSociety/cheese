// A friend's invite link (`/account/signup?code=…`) carries the code in its address.
// Signing up through a provider leaves the site and comes back to
// /account/oauth/complete with an address of its own, and going from the sign-up
// page to the sign-in page drops the query too — so the code that came in is kept
// in this tab and filled in wherever the account is finally created.

import { storageGet, storageSet } from '@/lib/browserStorage'

const KEY = 'cheese.inviteCode'

/** The code in a `?code=` query value, or '' when there is none. */
function fromQuery(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

/**
 * The invite code to fill in: the one in this address, else one an earlier page of
 * this tab arrived with. A code in the address is remembered for the pages after it.
 */
export function inviteCodeFor(query: unknown): string {
  const code = fromQuery(query)
  if (code) {
    storageSet(KEY, code, 'session')
    return code
  }
  return storageGet(KEY, 'session') ?? ''
}
