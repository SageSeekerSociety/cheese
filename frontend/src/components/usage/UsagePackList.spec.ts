// 「其他额度」列表：最后一次放行的调用可以把一个包透支一点，透支的债留在账上，
// 负数照实写出来，但不能四舍五入成 -0 —— 那样既不像 0，也没说欠了多少。
import type { UsagePack } from '@/lib/creditUsage'

import { render } from '@testing-library/vue'
import { describe, expect, it } from 'vitest'

import UsagePackList from './UsagePackList.vue'

import i18n, { setLocale } from '@/i18n'

setLocale('zh-CN')

const PACK: UsagePack = {
  id: 'p1',
  source: 'admin_grant',
  project_id: null,
  task_id: null,
  project_name: null,
  task_name: null,
  credits_total: 100,
  credits_remaining: 0,
  remaining_ratio: 0,
  expires_at: null,
}

function mount(packs: UsagePack[]) {
  return render(UsagePackList, { props: { packs }, global: { plugins: [i18n] } })
}

function fillOf(container: HTMLElement): HTMLElement {
  return container.querySelector('.upk__fill') as HTMLElement
}

describe('其他额度', () => {
  it('透支了一点时照实写欠了多少，不写成 -0', () => {
    const ui = mount([{ ...PACK, credits_remaining: -0.04, remaining_ratio: -0.0004 }])

    expect(ui.getByText('剩余 -0.04 点')).toBeTruthy()
    expect(ui.queryByText('剩余 -0 点')).toBeNull()
    expect(fillOf(ui.container).style.width).toBe('0%')
  })

  it('还剩一点的包照实写剩余和条长', () => {
    const ui = mount([{ ...PACK, credits_remaining: 8.5, remaining_ratio: 0.085 }])

    expect(ui.getByText('剩余 8.5 点')).toBeTruthy()
    expect(fillOf(ui.container).style.width).toBe('8.5%')
  })
})
