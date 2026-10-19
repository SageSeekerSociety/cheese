import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render } from '@testing-library/vue'
import { afterEach, expect, it } from 'vitest'

import ComputeView from './ComputeView.vue'

import i18n, { setLocale } from '@/i18n'

afterEach(cleanup)
it('keeps a failed read distinct from an empty list and allows retry', async () => {
  setLocale('zh-CN')
  const view = render(ComputeView, {
    props: {
      devices: [],
      canManage: false,
      loading: false,
      loadFailed: true,
      reason: 'Service unavailable',
      forbidden: false,
      busy: null,
      desktop: false,
      command: '',
      error: null,
    },
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
  expect(view.queryByText('暂无设备')).toBeNull()
  await fireEvent.click(view.getByRole('button', { name: '重试' }))
  expect(view.emitted().load).toHaveLength(1)
})
