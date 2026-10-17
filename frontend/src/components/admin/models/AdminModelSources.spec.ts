/**
 * 详情抽屉里的「线路」一段：config 声明的线路只读、运行时的能动、「测试」先灰着。
 *
 * 模子照 `AdminModelDetailDrawer.spec.ts`：vue-i18n 的键透传，断言直接写键名。
 */
import type { SourceRow } from '@/lib/adminModels'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, within } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('vue-i18n', async (importOriginal) => {
  const actual = await importOriginal<typeof import('vue-i18n')>()
  return { ...actual, useI18n: () => ({ t: (key: string) => key }) }
})

import AdminModelSources from './AdminModelSources.vue'

function source(over: Partial<SourceRow> = {}): SourceRow {
  return {
    source_id: 'rt-1',
    origin: 'runtime',
    blocked: false,
    order: 2,
    protocol: 'openai-chat',
    upstream_model: 'kimi-k3-go',
    upstream_raw: 'hosted_vllm/kimi-k3-go',
    host: 'opencode.ai',
    prices: { input: 1e-6, output: 4e-6 },
    free: false,
    priced: true,
    header_names: ['user-agent'],
    session_header: 'x-opencode-session',
    ...over,
  }
}

const CONFIG = source({
  source_id: 'cfg-1',
  origin: 'config',
  order: 3,
  protocol: 'anthropic',
  upstream_model: 'kimi-k3-official',
  header_names: [],
  session_header: null,
})

function mount(sources: SourceRow[]) {
  return render(AdminModelSources, {
    props: { sources },
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

function item(page: ReturnType<typeof mount>, upstream: string) {
  return within(page.getByText(upstream).closest('li')!)
}

afterEach(() => cleanup())

describe('线路', () => {
  it('config 声明的线路列出来，但不给任何改它的按钮', () => {
    const page = mount([source(), CONFIG])
    const config = item(page, 'kimi-k3-official')
    for (const action of ['up', 'down', 'edit', 'block', 'delete']) {
      expect(config.queryByRole('button', { name: `models.source.action.${action}` })).toBeNull()
    }
    expect(config.getByText('models.source.readonly')).toBeTruthy()
    const runtime = item(page, 'kimi-k3-go')
    expect(runtime.getByRole('button', { name: 'models.source.action.edit' })).toBeTruthy()
  })

  it('提前把这条线路的优先级减一，延后加一', async () => {
    const page = mount([source()])
    const runtime = item(page, 'kimi-k3-go')

    await fireEvent.click(runtime.getByRole('button', { name: 'models.source.action.up' }))
    await fireEvent.click(runtime.getByRole('button', { name: 'models.source.action.down' }))

    const moves = page.emitted('move') as [SourceRow, number][]
    expect(moves.map(([s, order]) => [s.source_id, order])).toEqual([
      ['rt-1', 1],
      ['rt-1', 3],
    ])
  })

  it('已经排在最前（优先级 1）就不能再提前', () => {
    const page = mount([source({ order: 1 })])
    const up = item(page, 'kimi-k3-go').getByRole('button', { name: 'models.source.action.up' })
    expect((up as HTMLButtonElement).disabled).toBe(true)
  })

  it('「测试」在一致性测试套件上线前是灰的', () => {
    const page = mount([source(), CONFIG])
    for (const test of page.getAllByRole('button', { name: 'models.source.action.test' })) {
      expect((test as HTMLButtonElement).disabled).toBe(true)
    }
  })
})
