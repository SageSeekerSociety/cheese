// The collaboration service's process: configuration from the environment,
// then listen. Hocuspocus stops on SIGTERM itself, storing every document with
// unsaved changes before it exits.

import { createCollabServer } from './service'

function required(name: string): string {
  const value = process.env[name]
  if (!value) throw new Error(`${name} is not set`)
  return value
}

const server = createCollabServer({
  port: Number(process.env.PORT ?? 8902),
  backendUrl: required('COLLAB_BACKEND_URL'),
  secret: required('COLLAB_SECRET'),
  redisUrl: process.env.REDIS_URL || undefined,
  quiet: true,
})

void server.listen().then(() => console.log(`[collab] listening on ${server.httpURL}`))
