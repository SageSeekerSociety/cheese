// 分享页：人说得出的几条——文档里资料库的图片经这条链接取，不用登录；停止分享以后
// 打开，说链接已失效。
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import { render, waitFor } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { t } from '@/i18n'

const read = vi.hoisted(() => vi.fn())
vi.mock('@/api/documentShares', async (actual) => ({
  ...(await actual<typeof import('@/api/documentShares')>()),
  readSharedDocument: read,
}))
vi.mock('./SharedDocumentView.vue', async () => {
  const { defineComponent, h } = await import('vue')
  return {
    default: defineComponent({
      props: { doc: { type: Object, default: null }, error: { type: String, default: '' } },
      setup: (props) => () =>
        h('div', { 'data-error': props.error }, (props.doc as { content?: string } | null)?.content ?? ''),
    }),
  }
})

import SharedDocument from './SharedDocument.vue'

afterEach(() => read.mockReset())

const shown = (token: string) =>
  render(SharedDocument, { props: { token }, global: { plugins: [createVuetify({ components })] } }).container

describe('SharedDocument', () => {
  it('资料库里的图片经这条链接取', async () => {
    read.mockResolvedValue({
      title: '组会纪要',
      content: '![架构](library/架构.png)\n\n![外链](https://example.com/a.png)\n',
      updated_at: '2026-10-17T10:00:00Z',
    })
    const host = shown('tok')
    await waitFor(() => expect(host.textContent).toContain('/api/shared/'))
    const text = host.textContent ?? ''
    expect(text).toContain('/api/shared/tok/image?path=library%2F%E6%9E%B6%E6%9E%84.png')
    expect(text).toContain('https://example.com/a.png')
  })

  it('不让搜索引擎收录这一页', async () => {
    read.mockResolvedValue({ title: 'x', content: '', updated_at: '2026-10-17T10:00:00Z' })
    shown('tok')
    await waitFor(() =>
      expect(document.head.querySelector('meta[name="robots"]')?.getAttribute('content')).toContain('noindex')
    )
  })

  it('停止分享以后打开，说链接已失效', async () => {
    read.mockRejectedValue(Object.assign(new Error('gone'), { status: 404 }))
    const host = shown('old')
    await waitFor(() =>
      expect(host.querySelector('[data-error]')?.getAttribute('data-error')).toBe(t('publicSite.sharedDoc.gone'))
    )
  })
})
