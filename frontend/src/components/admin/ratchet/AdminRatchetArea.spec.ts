/**
 * 「较同口径起点」这一列怎么算 —— 它是页面上唯一一处前端自己判的地方。
 *
 * 服务端算出的方向（`direction`）前端只翻译，但那一列的两个端点是前端自己从点列里
 * 取的。取错了不会报错，只会安安静静地摆出一个谁也验不了的数：中间一次采集跑到一半
 * 没采到数（`actual: null`），跳过它去拿更早的 10 和之后的 3 相减，就是一次「少了 7」
 * 的改善 —— 而这三次采集里没有任何一次量到过那个变化。
 *
 * 所以这里钉住三件事，三件都是同一份判据（和 `backend/app/domain/ratchet/board.py` 的
 * `_direction` 一致）：
 *
 * 1. 洞断在中间 → 不比，并说明是「隔着一次没跑到」，不是「只有一个点」。
 * 2. 洞断在**末尾**（最近一次没采到）→ 同样不比。
 * 3. 没有洞 → 照常给起点、终点和差。
 *
 * 规则指纹那把刀（换过口径的两段不相减）由服务端判方向，这里不再复测，只造一个
 * 同段的点列。
 */
import type { Component } from 'vue'
import type { RatchetCheck, RatchetPoint } from '@/views/admin/ratchetApi'

import { render } from '@testing-library/vue'
import { describe, expect, it, vi } from 'vitest'

// 词条本身对不对由 `i18n/catalog.spec.ts` 管；这一组问的是画了哪一档，所以把要断言的
// 两个键翻回人话，其余原样返回键名（仓库里既有的做法，见 `views/admin/AdminLayout.spec.ts`）。
const WORDS: Record<string, string> = {
  'ratchet.value.holeBlocksCompare': '隔着一次没跑到',
  'ratchet.value.noSecondPoint': '只有一个点',
}
vi.mock('vue-i18n', async () => {
  const actual = await vi.importActual<typeof import('vue-i18n')>('vue-i18n')
  return { ...actual, useI18n: () => ({ t: (key: string) => WORDS[key] ?? key }) }
})

import AdminRatchetArea from './AdminRatchetArea.vue'

let clock = 0
/** 一次采集在归档里的一点。数缺席的那次写 `actual: null`（服务端就是这么存的），
 *  这也正是页面必须认出的形状。 */
function point(actual: number | null, fingerprint = 'fp-a'): RatchetPoint {
  clock += 1
  return {
    commit: `c${clock}0000000`,
    collected_at: `2026-09-30T0${clock}:00:00+00:00`,
    run_url: `https://example.test/run/${clock}`,
    collection: 'ok',
    status: actual === null ? 'not_collected' : 'pass',
    actual,
    frozen: 4,
    stale_count: null,
    rule_fingerprint: fingerprint,
    rule_changed: false,
    new_exemptions: null,
    details: null,
    reason: actual === null ? '这次没采到' : null,
  }
}

function check(points: RatchetPoint[], direction: RatchetCheck['direction']): RatchetCheck {
  const last = points[points.length - 1]
  return {
    id: 'fe-boundary',
    area: '边界',
    better: 'down',
    direction,
    status: last.status,
    actual: last.actual,
    frozen: last.frozen,
    stale: [],
    stale_count: null,
    rule_fingerprint: last.rule_fingerprint,
    points,
  }
}

function mount(entry: RatchetCheck) {
  return render(AdminRatchetArea as unknown as Component, {
    props: { area: '边界', checks: [entry], collections: entry.points.length },
    global: { stubs: { AdminRatchetSparkline: true } },
  })
}

/** 「较同口径起点」那一格。表里第 5 列，按表头顺序数出来 —— 用列序而不是类名，是
 *  因为这一格的三种画法（两个数、两句说明）本身没有标识类。 */
function changeCell(container: Element): Element {
  const rows = container.querySelectorAll('tbody tr')
  const cells = rows[0].querySelectorAll('td')
  return cells[4] as HTMLElement
}

describe('较同口径起点', () => {
  it('中间隔着一次没采到，就不跨过它去比', () => {
    const { container } = mount(check([point(10), point(null), point(3)], 'unknown'))
    expect(changeCell(container).textContent).toContain('隔着一次没跑到')
    expect(changeCell(container).textContent).not.toContain('→')
  })

  it('最近一次没采到，也不比 —— 数是从上一次的洞后面来的', () => {
    const { container } = mount(check([point(10), point(3), point(null)], 'unknown'))
    expect(changeCell(container).textContent).toContain('隔着一次没跑到')
    expect(changeCell(container).textContent).not.toContain('→')
  })

  it('只有一个点时才说「只有一个点」', () => {
    const { container } = mount(check([point(10)], 'unknown'))
    expect(changeCell(container).textContent).toContain('只有一个点')
  })

  it('没有洞就给起点、终点和差', () => {
    const { container } = mount(check([point(10), point(3)], 'improving'))
    const cell = changeCell(container).textContent ?? ''
    expect(cell).toContain('10')
    expect(cell).toContain('→')
    expect(cell).toContain('3')
    expect(cell).toContain('-7')
  })
})
