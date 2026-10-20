import type { MyDevice } from '@/types/devices'

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

it('says when an offline device was last online', () => {
  setLocale('zh-CN')
  const view = render(ComputeView, {
    props: {
      devices: [
        {
          device_id: 'd1',
          name: 'Lab computer',
          online: false,
          team_id: 7,
          screens: [],
          last_seen_at: new Date(Date.now() - 3 * 3600_000).toISOString(),
        } as MyDevice,
      ],
      canManage: false,
      loading: false,
      loadFailed: false,
      reason: null,
      forbidden: false,
      busy: null,
      desktop: false,
      command: '',
      error: null,
    },
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
  expect(view.getByText(/离线.*上次在线.*3/)).toBeTruthy()
})
