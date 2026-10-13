/**
 * 用量那一屏最近的账本行。规则：网关的一行是网关给一个模型合计的一段用量（可能是很多次
 * 请求），表上不能把它说成一次调用；订阅通路的一行就是一次请求，照常写。
 */
import type { StatsUsage } from '@/lib/adminStats'

import { createRouter, createWebHashHistory } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { beforeAll, describe, expect, it } from 'vitest'

import AdminDashboardUsageWho from './AdminDashboardUsageWho.vue'

import i18n, { setLocale } from '@/i18n'

beforeAll(() => {
  setLocale('zh-CN')
})

type Row = NonNullable<StatsUsage['recent']>[number]

function row(over: Partial<Row>): Row {
  return {
    at: '2026-10-12T17:00:00+08:00',
    project_id: null,
    project: '后端',
    handle: 'lin',
    model: 'glm-4.6',
    route: 'gateway',
    kind: 'chat',
    input_tokens: 1000,
    output_tokens: 10,
    cache_read_tokens: null,
    cost_usd: 0.01,
    ...over,
  }
}

type User = NonNullable<StatsUsage['top_users']>[number]

async function mountWho(recent: Row[], topUsers: User[] = []) {
  const router = createRouter({
    history: createWebHashHistory(),
    routes: [{ path: '/:any(.*)*', component: { template: '<div />' } }],
  })
  await router.push('/')
  const data = { top_users: topUsers, top_projects: [], recent } as unknown as StatsUsage
  return render(AdminDashboardUsageWho, {
    props: { data, loading: false },
    global: { plugins: [createVuetify({ components, directives }), i18n, router] },
  })
}

/** 「最近」那张表里，模型名所在的那一行。 */
function rowOf(container: Element, model: string): HTMLElement {
  const tables = container.querySelectorAll('table')
  const recent = tables[tables.length - 1]!
  return Array.from(recent.querySelectorAll('tbody tr')).find((tr) => tr.textContent?.includes(model)) as HTMLElement
}

describe('AdminDashboardUsageWho · 最近的账本行', () => {
  it('网关行标成汇总，订阅行不标；表头和说明不把一行叫作一次调用', async () => {
    const { container, getByText } = await mountWho([
      row({ model: 'glm-4.6', route: 'gateway', input_tokens: 8_680_000 }),
      row({ model: 'claude-opus-4-1', route: 'subscription' }),
    ])

    const gateway = rowOf(container, 'glm-4.6')
    expect(gateway.textContent).toContain('汇总')
    expect(gateway.querySelector('[title*="可能包含很多次请求"]')).toBeTruthy()
    expect(rowOf(container, 'claude-opus-4-1').textContent).not.toContain('汇总')

    expect(container.textContent).not.toContain('最近的调用')
    expect(getByText(/2 行用量记录/).textContent).not.toMatch(/2 次调用/)
  })
})

describe('AdminDashboardUsageWho · 触发最多的人', () => {
  it('每一行都带头像：挑过的拼 /avatars/{id}，队友走队友的脸', async () => {
    const { container } = await mountWho([], [
      { handle: 'lin', name: '林一', avatar_id: 9, is_agent: false, tokens: 5, calls: 2, cost_usd: 0.1, cache_hit: null },
      { handle: 'cheese-abc', name: '小苔', is_agent: true, tokens: 3, calls: 1, cost_usd: 0, cache_hit: null },
    ])

    const users = container.querySelectorAll('table')[0]!
    const rows = Array.from(users.querySelectorAll('tbody tr')) as HTMLElement[]
    expect(rows.length).toBe(2)
    // 人：自己挑的那张素材拼出来的图。
    expect(rows[0].querySelector('.identity-view'), '这一行没走共用身份视图').not.toBeNull()
    expect(rows[0].querySelector('img')?.getAttribute('src')).toContain('/avatars/9')
    // 队友（is_agent）走队友的脸，不画人的头像图。
    expect(rows[1].querySelector('.identity-view')).not.toBeNull()
    expect(rows[1].querySelector('img')).toBeNull()
  })
})
