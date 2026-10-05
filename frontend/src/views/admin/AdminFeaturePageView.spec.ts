/**
 * 功能数据入口壳**画的那一半**：认得出 id 就画那个功能页本身，认不出就一句「还没有这一页」。
 *
 * 「挑组件」在容器 `AdminFeaturePage.vue` 里（读地址、查注册表）；这里只收结果 —— 一个
 * 组件或 `null`。所以这一组挂的是「给一个组件」和「给 null」两头：后者不能画成空白，
 * 也不能静悄悄换掉地址 —— 那个 id 是从外面粘进来的。
 */
import { defineComponent } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { describe, expect, it, vi } from 'vitest'

vi.mock('vue-i18n', async () => {
  const actual = await vi.importActual<typeof import('vue-i18n')>('vue-i18n')
  return {
    ...actual,
    useI18n: () => ({
      t: (key: string, params?: Record<string, unknown>) => (params ? `${key} ${JSON.stringify(params)}` : key),
    }),
  }
})

import AdminFeaturePageView from './AdminFeaturePageView.vue'

/** 一个认得出的功能页：只画一句能认出来的话。 */
const FeatureStub = defineComponent({ template: `<div>功能页本体</div>` })

function mount(props: Record<string, unknown>) {
  return render(AdminFeaturePageView, {
    props,
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

describe('功能数据入口壳的画面', () => {
  it('认得出 id 时画的是那个功能页本身，不画「还没有这一页」', () => {
    const { getByText, queryByText } = mount({ id: 'topic-naming', feature: FeatureStub })

    expect(getByText('功能页本体')).toBeTruthy()
    expect(queryByText('featureStats.page.unknown')).toBeNull()
  })

  it('认不出 id 时画「还没有这一页」，并把 id 带进那句说明里', () => {
    const { getByText } = mount({ id: 'ghost', feature: null })

    expect(getByText('featureStats.page.unknown')).toBeTruthy()
    expect(getByText('featureStats.page.unknownDesc {"id":"ghost"}')).toBeTruthy()
  })
})
