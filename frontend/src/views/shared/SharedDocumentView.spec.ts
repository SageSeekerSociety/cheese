// 分享页的页脚：分享人自己的邀请码还有名额时，给一条带着这个码进注册页的链接；名额用完、
// 没有码、或不知道谁分享的，只指向知是首页。
import type { SharedBy } from '@/api/documentShares'

import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import { cleanup, render } from '@testing-library/vue'
import { afterEach, describe, expect, it } from 'vitest'

import SharedDocumentView from './SharedDocumentView.vue'

afterEach(cleanup)

async function shown(sharedBy: SharedBy | null, content = '正文') {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/:any(.*)*', component: { template: '<div />' } }],
  })
  await router.push('/s/tok')
  await router.isReady()
  const doc = { title: '组会纪要', content, updated_at: '2026-10-17T10:00:00Z', shared_by: sharedBy }
  return render(SharedDocumentView, {
    props: { doc, error: '' },
    global: { plugins: [router, createVuetify({ components })] },
  }).container
}

/** Where each link on the page goes, as the browser would read its address. */
const targets = (host: Element) =>
  [...host.querySelectorAll('a[href]')].map((a) => new URL(a.getAttribute('href')!, 'https://example.test'))

const signupLinks = (host: Element) => targets(host).filter((url) => url.pathname === '/account/signup')

describe('the shared page colophon', () => {
  it('names the author and offers their invite code while it has seats left', async () => {
    const host = await shown({ handle: 'alice', nickname: '爱丽丝', code: 'K7Q2M9', seats_left: 2 })
    const text = host.querySelector('.shared-colophon')?.textContent ?? ''
    expect(text).toContain('爱丽丝')
    expect(text).not.toMatch(/交互模型|Interactive models/)
    const links = signupLinks(host)
    expect(links).toHaveLength(1)
    expect(links[0].searchParams.get('code')).toBe('K7Q2M9')
  })

  it('keeps the author credit when seats are gone and links to the site root', async () => {
    const host = await shown({ handle: 'alice', nickname: null, code: 'K7Q2M9', seats_left: 0 })
    expect(host.querySelector('.shared-colophon')?.textContent).toContain('@alice')
    expect(signupLinks(host)).toHaveLength(0)
    expect(targets(host).some((url) => url.pathname === '/')).toBe(true)
  })

  it('mentions interactive models when the document embeds a mini-app', async () => {
    const host = await shown(
      { handle: 'alice', nickname: '爱丽丝', code: 'K7Q2M9', seats_left: 1 },
      '正文\n:::app k7q2m9@1 演示\n说明\n:::\n'
    )
    const text = host.querySelector('.shared-colophon')?.textContent ?? ''
    expect(text).toContain('爱丽丝')
    expect(text).toMatch(/交互模型|Interactive models/)
    expect(signupLinks(host)).toHaveLength(1)
  })

  it('offers no sign-up link when the sharer has no code or is unknown', async () => {
    expect(signupLinks(await shown({ handle: 'alice', nickname: null, code: null, seats_left: 3 }))).toHaveLength(0)
    cleanup()
    const host = await shown(null)
    expect(signupLinks(host)).toHaveLength(0)
    expect(targets(host).some((url) => url.pathname === '/')).toBe(true)
  })
})
