// 「分享…」：人说得出的几条——还没有链接时能创建；有了链接能复制、能停止；不能分享的人
// 看不到创建链接的按钮。
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, screen } from '@testing-library/vue'
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

import DocShareDialog from './DocShareDialog.vue'

import { t } from '@/i18n'

beforeAll(() => {
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
  vi.stubGlobal('visualViewport', {
    width: 1280,
    height: 900,
    offsetLeft: 0,
    offsetTop: 0,
    addEventListener() {},
    removeEventListener() {},
  })
  ;(window as unknown as { innerWidth: number }).innerWidth = 1280
})
afterAll(() => vi.unstubAllGlobals())
afterEach(() => {
  document.body.innerHTML = ''
})

function mount(props: Record<string, unknown>) {
  const handlers = { share: vi.fn(), unshare: vi.fn(), copy: vi.fn() }
  const Host = {
    components: { DocShareDialog },
    props: ['extra'],
    setup: () => handlers,
    template: `
      <v-app>
        <DocShareDialog :model-value="true" title="组会纪要" :loading="false" :may-share="true" :url="null"
          :busy="false" error="" v-bind="extra" @share="share" @unshare="unshare" @copy="copy" />
      </v-app>`,
  }
  render(Host, { props: { extra: props }, global: { plugins: [createVuetify({ components, directives })] } })
  return handlers
}

const button = (key: string) => screen.findByRole('button', { name: t(`work.room.doc.shareDialog.${key}`) })

describe('DocShareDialog', () => {
  it('还没有链接时，创建链接', async () => {
    const { share } = mount({})
    await fireEvent.click(await button('create'))
    expect(share).toHaveBeenCalled()
  })

  it('有了链接，能复制，也能停止分享', async () => {
    const { copy, unshare } = mount({ url: 'https://example.com/s/abc' })
    expect((screen.getByTestId('doc-share-url').querySelector('input') as HTMLInputElement).value).toBe(
      'https://example.com/s/abc'
    )
    await fireEvent.click(await button('copy'))
    expect(copy).toHaveBeenCalled()
    await fireEvent.click(await button('stop'))
    expect(unshare).toHaveBeenCalled()
  })

  it('不能分享的人没有创建链接的按钮', async () => {
    mount({ mayShare: false })
    await screen.findByText(t('work.room.doc.shareDialog.notAllowed'))
    expect(screen.queryByRole('button', { name: t('work.room.doc.shareDialog.create') })).toBeNull()
  })
})
