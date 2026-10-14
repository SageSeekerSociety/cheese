/**
 * 功能数据页的外壳：id 认得出来时画注册表里那一个组件；认不出来时说出是哪个 id、画一句
 * 「还没有这个功能的数据页」，而不是一张空页或者 404。
 *
 * 地址里那一段认不认得出来是两件不同的事（前者查前端注册表、后者查服务端清单），这一屏
 * 只管前者，所以这里直接喂 feature/undefined，不走路由。
 */
import type { FeatureView } from '@/views/admin/features/registry'

import { defineComponent, markRaw } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { describe, expect, it } from 'vitest'

import AdminFeaturePageView from './AdminFeaturePageView.vue'

import i18n from '@/i18n'

// 注册表里那个组件已经被注册表自己 `defineAsyncComponent` 过；这里换成同步的一小块，
// 好让断言看见的是「外壳挑了哪一个组件」，而不是那个功能自己取数取到没有。
// `markRaw`：props 会被 Vue 代理成响应式对象，组件对象跟着变成响应式的会报警告。
const DocsAssistant = markRaw(
  defineComponent({ name: 'DocsAssistantStub', template: '<div class="t-body">问芝士的数字</div>' })
)

function feature(over: Partial<FeatureView> = {}): FeatureView {
  return {
    id: 'docs-assistant',
    titleKey: 'featureStats.features.docsAssistant.title',
    summaryKey: 'featureStats.features.docsAssistant.summary',
    view: DocsAssistant,
    ...over,
  }
}

function mount(props: Record<string, unknown>) {
  return render(AdminFeaturePageView, {
    props,
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
}

describe('功能数据页的外壳', () => {
  it('id 认得出来时，画注册表里那一个组件，不说认不出来', () => {
    const { getByText, queryByText } = mount({ id: 'docs-assistant', feature: feature() })
    expect(getByText('问芝士的数字')).toBeTruthy()
    expect(queryByText(i18n.global.t('featureStats.page.unknown'))).toBeNull()
  })

  it('id 认不出来时，说出是哪一个 id', () => {
    const { getByText } = mount({ id: 'unknown-thing', feature: undefined })
    expect(getByText(i18n.global.t('featureStats.page.unknown'))).toBeTruthy()
    expect(getByText(i18n.global.t('featureStats.page.unknownDesc', { id: 'unknown-thing' }))).toBeTruthy()
  })

  it('认不出来时，页头仍然写着这一块的名字', () => {
    const { getByText } = mount({ id: 'unknown-thing', feature: undefined })
    expect(getByText(i18n.global.t('navigation.admin.featureStats'))).toBeTruthy()
  })
})
