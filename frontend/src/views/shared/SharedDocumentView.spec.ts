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

async function shown(sharedBy: SharedBy | null) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/:any(.*)*', component: { template: '<div />' } }],
  })
  await router.push('/s/tok')
  await router.isReady()
  const doc = { title: '组会纪要', content: '正文', updated_at: '2026-10-17T10:00:00Z', shared_by: sharedBy }
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
  it('offers the sharer’s invite code while it has seats left', async () => {
    const host = await shown({ handle: 'alice', nickname: '爱丽丝', code: 'K7Q2M9', seats_left: 2 })
    const links = signupLinks(host)
    expect(links).toHaveLength(1)
    expect(links[0].searchParams.get('code')).toBe('K7Q2M9')
  })

  it('offers no sign-up link once the seats are gone, only the way to the site', async () => {
    const host = await shown({ handle: 'alice', nickname: null, code: 'K7Q2M9', seats_left: 0 })
    expect(signupLinks(host)).toHaveLength(0)
    expect(targets(host).some((url) => url.pathname === '/')).toBe(true)
  })

  it('offers no sign-up link when the sharer has no code or is unknown', async () => {
    expect(signupLinks(await shown({ handle: 'alice', nickname: null, code: null, seats_left: 3 }))).toHaveLength(0)
    cleanup()
    const host = await shown(null)
    expect(signupLinks(host)).toHaveLength(0)
    expect(targets(host).some((url) => url.pathname === '/')).toBe(true)
  })
})
