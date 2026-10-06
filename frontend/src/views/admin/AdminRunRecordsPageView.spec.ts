/**
 * 运行记录这一屏：报错和平台自己处理掉的事分得开，点开一种就去取它的详情；接口读失败
 * 不画成「暂无运行记录」。
 */
import type { RunRecordGroup, RunRecordOverview } from '@/views/admin/runRecordsApi'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { describe, expect, it } from 'vitest'

import AdminRunRecordsPageView from './AdminRunRecordsPageView.vue'

import i18n from '@/i18n'

function group(kind: string, key: string): RunRecordGroup {
  return {
    key,
    kind,
    severity: kind.endsWith('error') ? 'error' : 'info',
    title: `${kind} ${key}`,
    count: 3,
    projects: 2,
    first_at: '2026-10-06T09:00:00Z',
    last_at: '2026-10-06T10:00:00Z',
    buckets: Array.from({ length: 24 }, () => 0),
  }
}

const overview: RunRecordOverview = {
  window: '24h',
  since: '2026-10-05T10:00:00Z',
  totals: { error_kinds: 1, errors: 3, recovered: 3 },
  groups: [group('backend_error', 'fp-site'), group('api_retry', 'retry')],
}

function mount(props: Record<string, unknown>) {
  return render(AdminRunRecordsPageView, {
    props,
    global: {
      plugins: [createVuetify({ components, directives }), i18n],
      stubs: { AppPage: { template: '<div><slot name="extra" /><slot /></div>' } },
    },
  })
}

describe('运行记录', () => {
  it('点开一种，就请容器取它的详情', async () => {
    const { getByText, emitted } = mount({ overview })
    await fireEvent.click(getByText('backend_error fp-site'))
    expect((emitted().select as unknown[][])[0][0]).toMatchObject({ key: 'fp-site', kind: 'backend_error' })
  })

  it('读失败时不说「暂无运行记录」', () => {
    const empty = i18n.global.t('admin.runRecords.empty')
    const nothing = mount({ overview: { ...overview, groups: [] } })
    expect(nothing.queryByText(empty)).not.toBeNull()
    nothing.unmount()
    expect(mount({ overview: null, failed: true }).queryByText(empty)).toBeNull()
  })
})
