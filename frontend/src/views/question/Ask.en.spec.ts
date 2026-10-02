/** 界面切到英文时，提问页不能再漏出中文：标题框、悬赏、提问指南、校验提示。 */
import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

vi.mock('@/components/common/Editor/Editor.vue', () => ({
  default: defineComponent({ setup: () => () => h('div') }),
}))
vi.mock('@/components/common/TopicSelector.vue', () => ({
  default: defineComponent({ setup: () => () => h('div') }),
}))
vi.mock('@/network/api/questions', () => ({ QuestionApi: { ask: vi.fn() } }))

import Ask from './Ask.vue'

import i18n, { setLocale } from '@/i18n'

const CJK = /[㐀-䶿一-鿿豈-﫿]/

beforeEach(() => setLocale('en'))
afterEach(() => {
  cleanup()
  setLocale('zh-CN')
})

it('reads in English, with the bounty open and the form rejected', async () => {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/:any(.*)*', component: { template: '<div />' } }],
  })
  const { baseElement, getByText, getByLabelText } = render(Ask, {
    global: { plugins: [router, createVuetify({ components, directives }), i18n] },
  })
  getByText('How to ask a good question')
  await fireEvent.click(getByText('Add bounty'))
  getByText('Bounty 1')
  await fireEvent.update(getByLabelText('Question title'), 'Why does the build fail')
  await fireEvent.click(getByText('Post question'))
  await waitFor(() => getByText('End the title with a question mark'))
  const attrs = Array.from(baseElement.querySelectorAll('[title],[aria-label],[placeholder]')).flatMap((el) =>
    ['title', 'aria-label', 'placeholder'].map((a) => el.getAttribute(a) ?? '')
  )
  expect([baseElement.textContent ?? '', ...attrs].filter((s) => CJK.test(s))).toEqual([])
})
