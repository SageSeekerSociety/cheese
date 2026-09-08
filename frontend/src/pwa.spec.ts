// @vitest-environment node
import { describe, expect, it, vi } from 'vitest'

const captured = vi.hoisted(() => ({ options: null as unknown }))
vi.mock('vite-plugin-pwa', () => ({
  VitePWA: (options: unknown) => {
    captured.options = options
    return { name: 'pwa-config-test' }
  },
}))

import '../vite.config'

type NavigationRoute = {
  urlPattern: (context: { url: URL; request: { mode: string }; sameOrigin: boolean }) => boolean
  handler: string
  options: { fetchOptions: { cache: string }; precacheFallback: { fallbackURL: string } }
}

describe('online workspace navigation', () => {
  it('fetches current HTML for deep links and retains the precached offline fallback', () => {
    const { workbox } = captured.options as {
      workbox: { navigateFallback: unknown; runtimeCaching: NavigationRoute[] }
    }
    expect(workbox.navigateFallback).toBeNull()
    const route = workbox.runtimeCaching[0]
    for (const path of ['/', '/about', '/projects/project/docs', '/spaces/4/tasks/12/submit']) {
      expect(
        route.urlPattern({
          url: new URL(path, 'https://example.test'),
          request: { mode: 'navigate' },
          sameOrigin: true,
        })
      ).toBe(true)
    }
    for (const path of ['/api/tasks/12', '/connector/ws', '/users/auth/login']) {
      expect(
        route.urlPattern({
          url: new URL(path, 'https://example.test'),
          request: { mode: 'navigate' },
          sameOrigin: true,
        })
      ).toBe(false)
    }
    expect(route.handler).toBe('NetworkOnly')
    expect(route.options.fetchOptions.cache).toBe('no-cache')
    expect(route.options.precacheFallback.fallbackURL).toBe('index.html')
  })
})
