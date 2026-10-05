/**
 * 「功能数据」目录页**画的那一半**：骨架、读失败、空、有目录四档。
 *
 * 四档的边界是这一页的全部意义：还在读（骨架）、这次读挂了（错误 + 重试）、读到了但
 * 没有功能（空），和读到一张目录。把「读挂了」画成「空」，「暂时读不到」和「本来就没
 * 有」在屏幕上就分不开 —— 这一组钉的就是这条分界。
 *
 * 每一行要么可点（`to` 有值），要么是静的（没页面的功能）；静的那一行说「还没做」，
 * 不是「点不动」。
 */
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { describe, expect, it, vi } from 'vitest'

vi.mock('vue-i18n', async () => {
  const actual = await vi.importActual<typeof import('vue-i18n')>('vue-i18n')
  return {
    ...actual,
    useI18n: () => ({ t: (key: string) => key }),
  }
})

import AdminFeatureStatsPageView from './AdminFeatureStatsPageView.vue'

const ROWS = [
  { id: 'topic-naming', to: { path: '/admin/feature-stats/topic-naming' }, title: '话题命名', summary: '给话题起名' },
  { id: 'docs-assistant', to: null, title: '文档助手', summary: '还没做' },
]

function mount(props: Record<string, unknown>) {
  return render(AdminFeatureStatsPageView, {
    props,
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

describe('功能数据目录页的画面', () => {
  it('还在读时画骨架，不画目录也不画空', () => {
    const { getByText, queryByText, container } = mount({ loading: true, loadError: null, rows: [] })

    expect(getByText('featureStats.page.noNumbers')).toBeTruthy()
    expect(container.querySelectorAll('.afs__bone')).toHaveLength(3)
    expect(queryByText('featureStats.page.empty')).toBeNull()
    expect(queryByText('featureStats.page.loadFailed')).toBeNull()
  })

  it('读失败画错误和重试，不画成空（原话取不到、是空串时也一样）', () => {
    const { getByText, queryByText } = mount({ loading: false, loadError: '', rows: [] })

    expect(getByText('featureStats.page.loadFailed')).toBeTruthy()
    expect(getByText('featureStats.page.retry')).toBeTruthy()
    expect(queryByText('featureStats.page.empty')).toBeNull()
  })

  it('重试那颗按钮往上发一次 retry', async () => {
    const { emitted, getByText } = mount({ loading: false, loadError: '服务器 500', rows: [] })

    await fireEvent.click(getByText('featureStats.page.retry'))
    expect(emitted('retry')).toEqual([[]])
  })

  it('读到了但没有功能时画空', () => {
    const { getByText, queryByText } = mount({ loading: false, loadError: null, rows: [] })

    expect(getByText('featureStats.page.empty')).toBeTruthy()
    expect(queryByText('featureStats.page.loadFailed')).toBeNull()
  })

  it('有目录时逐行画：有页面的那行可点，没页面的那行是静的', () => {
    const { getByText, queryByText } = mount({ loading: false, loadError: null, rows: ROWS })

    expect(getByText('话题命名')).toBeTruthy()
    expect(getByText('给话题起名')).toBeTruthy()
    expect(getByText('文档助手')).toBeTruthy()
    // 「还没做」只挂在没页面那一行上。
    expect(getByText('featureStats.page.notBuilt')).toBeTruthy()
    expect(queryByText('featureStats.page.empty')).toBeNull()
  })
})
