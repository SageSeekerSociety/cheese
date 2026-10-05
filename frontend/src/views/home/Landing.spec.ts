import { existsSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { reactive } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor, within } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import Download from './Download.vue'
import Landing from './Landing.vue'
import Solutions from './Solutions.vue'

import i18n, { resolveInitialLocale, setLocale } from '@/i18n'
import HomeRoutes from '@/router/home'
import AccountService from '@/services/account'

vi.mock('@/services/account', () => ({ default: reactive({ loggedIn: false }) }))
// The download page reads the published version and changelog; this site has none.
// 登录后的根地址先问一遍项目清单：一个项目都没有，就落在待办上。
vi.mock('@/api', () => ({ listProjects: async () => ({ data: [] }) }))
vi.mock('@/lib/desktopChangelog', () => ({ fetchDesktopRelease: async () => ({ version: null, days: [] }) }))
// happy-dom has no IntersectionObserver; the scroll-driven room simply stays on its first step.
vi.stubGlobal(
  'IntersectionObserver',
  class {
    observe() {}
    disconnect() {}
  }
)
// The download page's other builds are in a menu, a VOverlay, which happy-dom
// cannot place without these.
beforeAll(() => {
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
  if (!('devicePixelRatio' in globalThis)) {
    Object.defineProperty(globalThis, 'devicePixelRatio', { configurable: true, value: 1 })
  }
  if (!globalThis.visualViewport) {
    Object.defineProperty(globalThis, 'visualViewport', {
      configurable: true,
      value: { width: 1024, height: 768, offsetLeft: 0, offsetTop: 0, addEventListener() {}, removeEventListener() {} },
    })
  }
})
beforeEach(() => setLocale('zh-CN'))

afterEach(() => {
  cleanup()
  AccountService.loggedIn = false
})

async function mount(path = '/') {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/account/signin', name: 'SignIn', component: { template: '<div>SignIn</div>' } },
      ...HomeRoutes.children!.map((route) => ({
        path: `/${route.path}`,
        name: route.name,
        meta: route.meta,
        beforeEnter: route.beforeEnter,
        component:
          route.name === 'Solutions'
            ? Solutions
            : route.name === 'Download'
              ? Download
              : route.meta?.publicLanding
                ? Landing
                : { template: '<div>Workspace</div>' },
      })),
    ],
  })
  await router.push(path)
  const view = render(
    { template: '<router-view />' },
    { global: { plugins: [router, createVuetify({ components, directives })] } }
  )
  return { ...view, router }
}

describe('公开首页', () => {
  it('switches to English without losing the chosen solution, and remembers the language', async () => {
    const view = await mount('/solutions')
    await fireEvent.click(view.getByRole('tab', { name: '企业' }))
    await fireEvent.click(view.getByRole('button', { name: 'Switch to English' }))
    expect(document.documentElement.lang).toBe('en')
    expect(resolveInitialLocale()).toBe('en')
    expect(view.getByRole('tab', { name: 'Companies' }).getAttribute('aria-selected')).toBe('true')
    for (const link of view.getAllByRole('link', { name: /Get started/ })) {
      expect(link.getAttribute('href')).toBe('/account/signin')
    }
    await fireEvent.click(view.getByRole('button', { name: '切换到中文' }))
    expect(i18n.global.locale.value).toBe('zh-CN')
  })

  it('moves between solutions with the arrow keys, and the panel follows the selected tab', async () => {
    const view = await mount('/solutions')
    const first = view.getByRole('tab', { name: '高校与机构' })
    expect(first.getAttribute('aria-selected')).toBe('true')
    await fireEvent.keyDown(first, { key: 'ArrowRight' })
    const company = view.getByRole('tab', { name: '企业' })
    expect(company.getAttribute('aria-selected')).toBe('true')
    expect(document.activeElement).toBe(company)
    await waitFor(() =>
      expect(view.getByRole('tabpanel').getAttribute('aria-labelledby')).toBe(company.getAttribute('id'))
    )
    await fireEvent.keyDown(company, { key: 'End' })
    expect(view.getByRole('tab', { name: '科研与创新团队' }).getAttribute('aria-selected')).toBe('true')
    await fireEvent.keyDown(document.activeElement!, { key: 'ArrowRight' })
    expect(first.getAttribute('aria-selected')).toBe('true')
  })

  it('sends organisations to the solutions page, where the way in is a conversation', async () => {
    const home = await mount()
    expect(home.queryByRole('tab', { name: '高校与机构' })).toBeNull()
    for (const link of home.getAllByRole('link', { name: /查看方案/ })) {
      expect(link.getAttribute('href')).toBe('/solutions')
    }
    expect(home.getByRole('link', { name: '方案' }).getAttribute('href')).toBe('/solutions')
    cleanup()

    const solutions = await mount('/solutions')
    const contacts = solutions.getAllByRole('link', { name: /预约交流/ })
    expect(contacts.length).toBeGreaterThan(0)
    for (const link of contacts) expect(link.getAttribute('href')).toBe('mailto:ops@okcheese.com')
    expect(solutions.getByRole('link', { name: '方案' }).getAttribute('aria-current')).toBe('page')
  })

  it('types one kind of project after another in front of 项目, and always comes back to the sentence', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    try {
      const t = (key: string) => i18n.global.t(`publicSite.${key}`)
      const sentence = t('positioning')
      const home = await mount()
      const line = home.getByText(sentence).closest('p')!
      const visible = () => line.querySelector('[aria-hidden="true"]:not(.hero-mod-sizer)')!.textContent!
      expect(visible()).toBe(sentence)

      const frames: string[] = []
      for (let ms = 0; ms < 40000; ms += 100) {
        await vi.advanceTimersByTimeAsync(100)
        frames.push(visible())
      }
      // Only the word in front of 项目 ever changes.
      for (const frame of frames) {
        expect(frame.startsWith(t('heroBefore'))).toBe(true)
        expect(frame.endsWith(t('heroAfter'))).toBe(true)
      }
      const shown = [1, 2, 3, 4, 5, 6].map((i) => frames.indexOf(t('heroBefore') + t(`heroKind${i}`) + t('heroAfter')))
      expect(shown.every((at) => at >= 0)).toBe(true)
      expect(frames.indexOf(sentence, Math.max(...shown))).toBeGreaterThan(Math.max(...shown))
    } finally {
      vi.useRealTimers()
    }
  })

  it('starts the sentence over in the new language when the language changes mid-cycle', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    try {
      const home = await mount()
      const zh = i18n.global.t('publicSite.positioning')
      const line = home.getByText(zh).closest('p')!
      const visible = () => line.querySelector('[aria-hidden="true"]:not(.hero-mod-sizer)')!.textContent!
      await vi.advanceTimersByTimeAsync(6000)
      expect(visible()).not.toBe(zh)
      await fireEvent.click(home.getByRole('button', { name: 'Switch to English' }))
      expect(visible()).toBe(i18n.global.t('publicSite.positioning'))
    } finally {
      vi.useRealTimers()
    }
  })

  it('shows the film on its poster, and plays it from the start with sound and no player controls when asked', async () => {
    const home = await mount()
    const video = home.getByRole('figure', { name: '影片：众智成事' }).querySelector('video')!
    expect(video.paused).toBe(true)
    expect(video.controls).toBe(false)
    video.currentTime = 12
    const caption = i18n.global.t('publicSite.filmCaption')
    await fireEvent.click(home.getByRole('button', { name: caption }))
    expect(video.muted).toBe(false)
    expect(video.currentTime).toBe(0)
    expect(video.controls).toBe(false)
    expect(home.queryByRole('button', { name: caption })).toBeNull()
  })

  it('leaves the Chinese-only film off the English page', async () => {
    setLocale('en')
    const home = await mount()
    expect(home.queryByRole('figure', { name: /Film/ })).toBeNull()
  })

  it('leads to the download page, which offers every build from this site, not from GitHub', async () => {
    const home = await mount()
    expect(home.getByRole('link', { name: '下载' }).getAttribute('href')).toBe('/download')
    cleanup()

    const view = await mount('/download')
    await fireEvent.click(view.getByRole('button', { name: '其他版本' }))
    const builds = await Promise.all(
      [/Mac（Apple 芯片）/, /Mac（Intel 芯片）/, /Windows/].map((name) => view.findByRole('link', { name }))
    )
    expect(builds.map((b) => b.getAttribute('href'))).toEqual([
      '/downloads/desktop/Cheese-arm64.dmg',
      '/downloads/desktop/Cheese-x64.dmg',
      '/downloads/desktop/Cheese-Setup-x64.exe',
    ])
    // The phone has no file to download: its row goes to the QR code on this page.
    expect((await view.findByRole('link', { name: /手机/ })).getAttribute('href')).toBe('#phone')
  })

  it('links the docs site from the top bar, in either language', async () => {
    const view = await mount('/solutions')
    expect(view.getByRole('link', { name: '文档' }).getAttribute('href')).toBe('/docs/')
    await fireEvent.click(view.getByRole('button', { name: 'Switch to English' }))
    expect(view.getByRole('link', { name: 'Docs' }).getAttribute('href')).toBe('/docs/')
  })

  it('says how to get past the system’s first-launch block only once a download has started', async () => {
    const view = await mount('/download')
    const download = await view.findByRole('link', { name: /下载 Mac 版/ })
    expect(view.queryByText(/仍要打开/)).toBeNull()
    await fireEvent.click(download)
    expect(await view.findByText(/仍要打开/)).toBeTruthy()
  })

  it('keeps the introduction open to signed-in users and links back to work', async () => {
    AccountService.loggedIn = true
    const view = await mount('/about')
    expect(view.router.currentRoute.value.path).toBe('/about')
    const links = view.getAllByRole('link', { name: /进入工作台/ })
    expect(links.length).toBeGreaterThan(0)
    for (const link of links) expect(link.getAttribute('href')).toBe('/')
    await view.router.push(links[0].getAttribute('href')!)
    expect(view.router.currentRoute.value.name).toBe('inbox')
  })

  it('updates the entry links after session restoration without navigating away', async () => {
    const view = await mount('/about')
    const signedOut = view.getAllByRole('link', { name: /开始使用/ })
    for (const link of signedOut) expect(link.getAttribute('href')).toBe('/account/signin')
    AccountService.loggedIn = true
    await waitFor(() => expect(view.getAllByRole('link', { name: /进入工作台/ })).toHaveLength(signedOut.length))
    expect(view.router.currentRoute.value.path).toBe('/about')
  })

  it('shows the public homepage when a signed-out user returns from work', async () => {
    AccountService.loggedIn = true
    const view = await mount('/')
    expect(view.router.currentRoute.value.name).toBe('inbox')
    AccountService.loggedIn = false
    await view.router.push('/')
    expect(view.router.currentRoute.value.path).toBe('/')
    expect(view.getAllByRole('link', { name: /开始使用/ }).length).toBeGreaterThan(0)
  })
})

// The solutions page is read by whoever decides to adopt the platform, so it may
// only offer what the product does. Courses and teaching units are gone, nobody
// joins a project as a mentor, and the off-site database backup means data does
// leave the mainland.
describe('what the solutions page offers', () => {
  async function solutionsText(tabs: string[]) {
    const view = await mount('/solutions')
    let text = document.body.textContent ?? ''
    for (const name of tabs) {
      const tab = view.getByRole('tab', { name })
      await fireEvent.click(tab)
      // The panel swaps out-in, so the old one lingers after the tab is selected.
      await waitFor(() => expect(view.getByRole('tabpanel').getAttribute('aria-labelledby')).toBe(tab.id))
      text += view.getByRole('tabpanel').textContent ?? ''
    }
    return { view, text }
  }

  it('offers no courses, teaching units, mentors or data residency, in Chinese', async () => {
    const { view, text } = await solutionsText(['高校与机构', '企业', '科研与创新团队'])
    for (const claim of ['教学单元', '导师', '开设课程', '出境']) expect(text).not.toContain(claim)

    // The sample problem card names the space it was posted in and when it is due.
    const card = view.getByText('校园知识检索助手').closest('.topic') as HTMLElement
    expect(within(card).getByText('空间')).toBeTruthy()
    expect(within(card).getByText('截止')).toBeTruthy()
    expect(within(card).queryByText('课程')).toBeNull()
  })

  it('offers no courses, teaching units, mentors or data residency, in English', async () => {
    setLocale('en')
    const { text } = await solutionsText(['Universities and institutions', 'Companies', 'Research teams'])
    for (const claim of [/\bunits?\b/i, /mentor/i, /open a course/i, /mainland/i]) expect(text).not.toMatch(claim)
  })
})

// What a teacher, a student, an office worker or a developer can do, one tab each,
// and the page of the manual that shows how.
describe('use cases on the homepage', () => {
  // A link into the docs site lands on a page only if the manual has its source.
  const manual = join(dirname(fileURLToPath(import.meta.url)), '../../../../docs/manual')
  const inManual = (href: string) => existsSync(join(manual, `${href.replace(/^\/docs\//, '')}.md`))

  it('opens on teachers and switches role by tab, each with a manual page that exists', async () => {
    const view = await mount()
    const section = within(view.getByRole('region', { name: '能用知是做什么' }))
    const roles = ['老师与助教', '学生', '办公', '开发团队']
    expect(section.getAllByRole('tab').map((tab) => tab.textContent?.trim())).toEqual(roles)
    expect(section.getByRole('tab', { name: '老师与助教' }).getAttribute('aria-selected')).toBe('true')
    expect(section.getByText('批准领取')).toBeTruthy()

    const links = new Set<string>()
    for (const role of roles) {
      const tab = section.getByRole('tab', { name: role })
      await fireEvent.click(tab)
      const panel = await waitFor(() => {
        const current = section.getByRole('tabpanel')
        expect(current.getAttribute('aria-labelledby')).toBe(tab.id)
        return current
      })
      expect(within(panel).getAllByRole('listitem').length).toBeGreaterThanOrEqual(3)
      const href = within(panel).getByRole('link').getAttribute('href')!
      expect(href).toMatch(/^\/docs\/[a-z-]+$/)
      expect(inManual(href), `${role} links to ${href}, which the manual does not have`).toBe(true)
      links.add(href)
    }
    expect(links.size).toBe(roles.length)
  })

  it('moves between roles with the arrow keys', async () => {
    const view = await mount()
    const section = within(view.getByRole('region', { name: '能用知是做什么' }))
    const teachers = section.getByRole('tab', { name: '老师与助教' })
    await fireEvent.keyDown(teachers, { key: 'ArrowLeft' })
    const developers = section.getByRole('tab', { name: '开发团队' })
    expect(developers.getAttribute('aria-selected')).toBe('true')
    expect(document.activeElement).toBe(developers)
    await fireEvent.keyDown(developers, { key: 'Home' })
    expect(teachers.getAttribute('aria-selected')).toBe('true')
  })

  it('is reachable from the top bar on every public page, in either language', async () => {
    const home = await mount()
    expect(home.getByRole('link', { name: '场景' }).getAttribute('href')).toBe('/#use-cases')
    cleanup()

    const solutions = await mount('/solutions')
    await fireEvent.click(solutions.getByRole('button', { name: 'Switch to English' }))
    const link = solutions.getByRole('link', { name: 'Use cases' })
    expect(link.getAttribute('href')).toBe('/#use-cases')
    await fireEvent.click(link)
    await waitFor(() => expect(solutions.router.currentRoute.value.fullPath).toBe('/#use-cases'))
    expect(await solutions.findByRole('region', { name: 'What you can do with Cheese' })).toBeTruthy()
    expect(solutions.getByRole('tab', { name: 'Developers' })).toBeTruthy()
  })
})

// The desktop app is used by people who have it installed already: the pages
// written to win them over have no place in its window, which has no way back
// from them either.
describe('inside the desktop app', () => {
  beforeEach(() => {
    ;(window as unknown as { __TAURI__?: unknown }).__TAURI__ = { core: { invoke: vi.fn() } }
  })
  afterEach(() => {
    delete (window as unknown as { __TAURI__?: unknown }).__TAURI__
  })

  it.each(['/', '/about', '/solutions', '/download'])('%s asks a signed-out person to sign in', async (path) => {
    const view = await mount(path)
    await waitFor(() => expect(view.router.currentRoute.value.name).toBe('SignIn'))
  })

  it.each(['/about', '/solutions', '/download'])('%s takes a signed-in person back to work', async (path) => {
    AccountService.loggedIn = true
    const view = await mount(path)
    await waitFor(() => expect(view.router.currentRoute.value.name).toBe('inbox'))
  })
})
