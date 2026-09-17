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

// 更新策略和安装信息都写在这个配置里，而它们的错法都是**安静的**：多写一行
// skipWaiting 就是「每次发版开着的页面自己刷新」，少写一条 manifest 字段就是
// 「手机上装出来的东西没有名字/没有快捷方式」。所以在这里钉住。
describe('新旧版本交接与安装信息', () => {
  type Workbox = {
    skipWaiting?: unknown
    clientsClaim?: unknown
    globIgnores: string[]
  }
  type Manifest = {
    id?: string
    categories?: string[]
    shortcuts?: { name: string; url: string }[]
    screenshots?: { src: string; sizes: string; form_factor?: string }[]
    start_url?: string
    scope?: string
  }

  const options = () => captured.options as { registerType: string; workbox: Workbox; manifest: Manifest }

  it('更新要问过用户：registerType 是 prompt，且没有偷偷 skipWaiting', () => {
    expect(options().registerType).toBe('prompt')
    // 这两个值若被重新写上，新 worker 会立刻接管，提示条根本没机会出现。
    expect(options().workbox.skipWaiting).toBeUndefined()
    expect(options().workbox.clientsClaim).toBeUndefined()
  })

  it('安装信息：有稳定的应用身份、分类、以及两个进得去的快捷方式', () => {
    const { manifest } = options()
    expect(manifest.id).toBe('/')
    expect(manifest.categories).toContain('productivity')
    // 快捷方式的 url 必须在 scope 里，而且要是打开就能到的顶层路由。
    const urls = (manifest.shortcuts ?? []).map((s) => s.url)
    expect(urls).toContain('/inbox')
    expect(urls).toContain('/spaces')
    for (const url of urls) expect(url.startsWith(manifest.scope ?? '/')).toBe(true)
  })

  it('安装截图只被安装弹窗取用，不进 precache', () => {
    const { workbox, manifest } = options()
    const shots = manifest.screenshots ?? []
    expect(shots.length).toBeGreaterThan(0)
    for (const shot of shots) {
      // 每一张都得有尺寸（浏览器按它挑图），并且整条路径被排除在预缓存之外。
      expect(shot.sizes).toMatch(/^\d+x\d+$/)
      expect(shot.src).toMatch(/^screenshots\//)
      expect(workbox.globIgnores).toContain('screenshots/**')
    }
  })
})
