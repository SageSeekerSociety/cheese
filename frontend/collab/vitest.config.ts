// The collaboration service's tests: a real Hocuspocus server and real
// providers over real sockets, in Node — not the app's happy-dom, and without
// the app's fetch trap (src/test/setup-network.ts), since talking to a backend
// over HTTP is exactly what the service does.
import { defineConfig } from 'vitest/config'

export default defineConfig({
  test: {
    root: __dirname,
    environment: 'node',
    include: ['**/*.spec.ts'],
    testTimeout: 20_000,
  },
})
