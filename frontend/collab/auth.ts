// Who is on the other end. Two kinds of caller and two derived keys, both from
// the secret shared with the backend (backend/app/domain/living_doc/collab.py):
// a browser shows a ticket the backend signed for one person and one document;
// the backend shows the bearer only the two services know. A ticket never
// works as the bearer.

import { createHash, createHmac, timingSafeEqual } from 'node:crypto'

export interface Ticket {
  /** The document the ticket opens. */
  doc: string
  /** The person's (or agent's) handle. */
  sub: string
  agent: boolean
  /** Read-only: the document is shown, and any change the client sends is dropped. */
  ro: boolean
  exp: number
}

export function deriveKey(secret: string, purpose: 'ticket' | 'internal'): string {
  return createHash('sha256').update(`${secret}:cheese-collab-${purpose}`).digest('hex')
}

function same(a: string, b: string): boolean {
  const left = Buffer.from(a)
  const right = Buffer.from(b)
  return left.length === right.length && timingSafeEqual(left, right)
}

/** The ticket's claims, or an error saying why it opens nothing. */
export function verifyTicket(token: string, key: string, now = Date.now() / 1000): Ticket {
  const parts = token.split('.')
  if (parts.length !== 3) throw new Error('malformed ticket')
  const [header, payload, signature] = parts
  const alg = JSON.parse(Buffer.from(header, 'base64url').toString()).alg
  if (alg !== 'HS256') throw new Error('unexpected ticket algorithm')
  const expected = createHmac('sha256', key).update(`${header}.${payload}`).digest('base64url')
  if (!same(signature, expected)) throw new Error('ticket signature does not match')
  const claims = JSON.parse(Buffer.from(payload, 'base64url').toString()) as Ticket
  if (typeof claims.exp !== 'number' || claims.exp < now) throw new Error('ticket expired')
  if (typeof claims.doc !== 'string' || typeof claims.sub !== 'string') throw new Error('ticket names nobody')
  return claims
}

export function verifyBearer(header: string | undefined, key: string): boolean {
  return !!header && same(header, `Bearer ${key}`)
}
