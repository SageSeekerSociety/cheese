/**
 * 平台健康那一行：没好的那一项要说出为什么没好（哪个定时任务失败了、缺哪个配置），
 * 只写一个 down 等于让读的人去翻日志。好的那几项照旧只写状态。
 */
import type { StatsPlatform } from '@/lib/adminStats'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render, screen } from '@testing-library/vue'
import { beforeAll, describe, expect, it } from 'vitest'

import AdminDashboardPlatform from './AdminDashboardPlatform.vue'

import i18n, { setLocale } from '@/i18n'
import { DASH_PLATFORM } from '@/views/demo/catalogDashboardFixtures'

beforeAll(() => {
  setLocale('zh-CN')
})

function mount(checks: StatsPlatform['health']['checks']) {
  const data: StatsPlatform = { ...DASH_PLATFORM, health: { overall: 'degraded', checks } }
  return render(AdminDashboardPlatform, {
    props: { data, days: 30, loading: false },
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
}

describe('AdminDashboardPlatform health', () => {
  it('a failing check says why it failed', () => {
    mount({
      database: { status: 'up' },
      periodic_jobs: { status: 'down', detail: 'routines: failed (LookupError)' },
      feedback_triage: { status: 'down', detail: '未设置 FEEDBACK_TRIAGE_TOPIC_ID' },
    })

    expect(screen.getByText('routines: failed (LookupError)')).toBeTruthy()
    expect(screen.getByText('未设置 FEEDBACK_TRIAGE_TOPIC_ID')).toBeTruthy()
    expect(screen.getByText('反馈分诊')).toBeTruthy()
  })

  it('a healthy check shows no reading as if it were a reason', () => {
    mount({ event_loop: { status: 'up', detail: 3.4 } })

    expect(screen.queryByText('3.4')).toBeNull()
  })
})
