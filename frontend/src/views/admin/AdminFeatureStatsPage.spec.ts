/**
 * 后台「功能数据」的目录页（`/admin/feature-stats`）。
 *
 * 这一页的规矩是**一个数字都没有** —— 数字在各自的功能页上，目录再放一遍就是第二份
 * 要跟着改的东西（第 4 条用例把这条规矩钉在渲染结果上，而不是只写在注释里）。
 *
 * 另外三件是「少说了一半」的坏法：
 *
 * 1. **读失败不能画成「还没有功能页」。** 拿不到清单和一条都没有是两句话，混起来之后
 *    接口挂了会被读成一切正常。
 * 2. **服务端有、前端还没做页面的功能也要列出来**（用服务端那句中文兜底），点进去画
 *    「这一页还没做」—— 从目录里凭空消失，读者会以为那个功能被下线了。
 * 3. **没有页面的那一行是静的**：不给链接、不进 Tab 顺序。半个链接比没有链接更糟。
 */
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const catalogue = vi.fn()
vi.mock('@/views/admin/features/featureApi', () => ({
  getFeatureCatalogue: (...args: unknown[]) => catalogue(...args),
}))

// 键名透传 + 参数照抄：断言直接写键名，插值也看得到（「占访客 {percent}」这类句子
// 里，那个数才是要断言的东西）。
vi.mock('vue-i18n', async (importOriginal) => {
  const actual = await importOriginal<typeof import('vue-i18n')>()
  return {
    ...actual,
    useI18n: () => ({
      t: (key: string, params?: Record<string, unknown>) => (params ? `${key} ${JSON.stringify(params)}` : key),
    }),
  }
})

import AdminFeatureStatsPage from './AdminFeatureStatsPage.vue'

const KNOWN = { id: 'docs-assistant', title: '问芝士', summary: '文档站的问答助手', view: 'docs-assistant' }
const UNKNOWN = { id: 'not-built-yet', title: '还没有页面的功能', summary: '服务端有、前端还没做', view: '' }

function mountPage() {
  return render(AdminFeatureStatsPage, {
    global: {
      plugins: [createVuetify({ components, directives })],
      // 画面那一半用 `NavLink` 画去处（组件边界的写法）：这里把这一层换成一个只读
      // `to` 画真 `<a>` 的替身，断言仍落在渲染出来的 href 上。
      stubs: { NavLink: { template: '<a :href="to"><slot /></a>', props: ['to'] } },
    },
  })
}

beforeEach(() => {
  catalogue.mockReset()
})

describe('功能数据的目录页', () => {
  it('按服务端给的顺序列出功能，已知的功能用 i18n 的文案', async () => {
    catalogue.mockResolvedValue({ features: [KNOWN, UNKNOWN] })
    const { findAllByRole, getByText } = mountPage()

    const rows = await findAllByRole('listitem')
    expect(rows).toHaveLength(2)
    expect(rows[0].textContent).toContain('featureStats.features.docsAssistant.title')
    expect(rows[0].textContent).toContain('featureStats.features.docsAssistant.summary')
    // 前端还没有页面的那个用服务端那句兜底 —— 不能从目录里消失。
    expect(getByText('还没有页面的功能')).toBeTruthy()
    expect(getByText('服务端有、前端还没做')).toBeTruthy()
  })

  it('页面渲染出来一个数字都没有', async () => {
    catalogue.mockResolvedValue({ features: [KNOWN, UNKNOWN] })
    const { findAllByRole, container } = mountPage()
    await findAllByRole('listitem')

    // 目录页的全部正文里不许出现数字（卡片上写「共 12 个功能」这类都不行）。
    expect(/\d/.test(container.textContent ?? '')).toBe(false)
  })

  it('有页面的功能给链接，没有页面的是静的一行', async () => {
    catalogue.mockResolvedValue({ features: [KNOWN, UNKNOWN] })
    const { findAllByRole, container, getByText } = mountPage()
    const rows = await findAllByRole('listitem')

    expect(rows[0].querySelector('a')?.getAttribute('href')).toBe('/admin/feature-stats/docs-assistant')
    expect(rows[1].querySelector('a')).toBeNull()
    expect(getByText('featureStats.page.notBuilt')).toBeTruthy()
    // 静的那一行不进 Tab 顺序：整页只有目录那一个链接可聚焦。
    expect(container.querySelectorAll('a')).toHaveLength(1)
  })

  it('读失败画「读不到清单」和重试，不画成「还没有功能页」', async () => {
    catalogue.mockRejectedValueOnce(new Error('boom'))
    const { findByText, queryByText } = mountPage()

    const retry = await findByText('featureStats.page.retry')
    expect(retry).toBeTruthy()
    expect(queryByText('featureStats.page.empty')).toBeNull()

    // 重试真的再取一次，成功之后画出清单。
    catalogue.mockResolvedValueOnce({ features: [KNOWN] })
    await fireEvent.click(retry)
    await waitFor(() => expect(catalogue).toHaveBeenCalledTimes(2))
  })

  it('清单是空的时候画「还没有功能页」', async () => {
    catalogue.mockResolvedValue({ features: [] })
    const { findByText } = mountPage()
    expect(await findByText('featureStats.page.empty')).toBeTruthy()
  })
})
