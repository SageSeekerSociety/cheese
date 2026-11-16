// 「提交与评分」里额度用量那一行：加得进来、加过就不能再加第二行、试算按后端的算法给分、
// 含自动算分那一行的阶段合计不是 100 时当场说清。
import type { TaskRubricLine } from '@/types'

import { defineComponent, h, ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest'

import TaskFormGrading from '../form/TaskFormGrading.vue'

import i18n, { setLocale } from '@/i18n'

// 「添加一项」那张小菜单是 VOverlay，happy-dom 里要这几样才画得出来。
beforeAll(() => {
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
  if (!globalThis.visualViewport) {
    Object.defineProperty(globalThis, 'visualViewport', {
      configurable: true,
      value: { width: 1024, height: 768, offsetLeft: 0, offsetTop: 0, addEventListener() {}, removeEventListener() {} },
    })
  }
})

beforeEach(() => setLocale('zh-CN'))
afterEach(cleanup)

function mount(initial: TaskRubricLine[] = []) {
  const rubric = ref<TaskRubricLine[]>([...initial])
  const items = ref<unknown[]>([])
  const Host = defineComponent({
    setup: () => () =>
      h(components.VApp, null, () =>
        h(TaskFormGrading, {
          rubric: rubric.value,
          'onUpdate:rubric': (next: TaskRubricLine[]) => (rubric.value = next),
          submissionSchema: items.value,
          'onUpdate:submissionSchema': (next: unknown[]) => (items.value = next),
          rubricPublic: true,
          'onUpdate:rubricPublic': () => undefined,
        })
      ),
  })
  render(Host, { global: { plugins: [createVuetify({ components, directives }), i18n] } })
  return { rubric }
}

const usageLine = (points: number, full = 0, zero = 100): TaskRubricLine => ({
  item: '',
  points,
  criteria: '',
  stage: null,
  kind: 'usage',
  config: { mode: 'threshold', full, zero },
})

async function openAddMenu() {
  await fireEvent.click(screen.getByTestId('task-form-add-line'))
  await screen.findByTestId('task-form-add-usage')
}

/** `data-testid` 落在 Vuetify 那层外壳（div.v-input）上，真正的框是它里面的 <input>。 */
const field = (id: string) => screen.getByTestId(id).querySelector('input') as HTMLInputElement

describe('额度用量那一行', () => {
  it('从「添加一项」的小菜单加得进来，加过之后就不能再加了', async () => {
    const { rubric } = mount()
    await openAddMenu()

    await fireEvent.click(screen.getByTestId('task-form-add-usage'))
    await waitFor(() => expect(rubric.value).toHaveLength(1))
    expect(rubric.value[0]).toMatchObject({ kind: 'usage', config: { mode: 'threshold' } })

    // 加过之后菜单里那一项是灰的：再点一次也加不出第二行。
    await openAddMenu()
    expect(screen.getByTestId('task-form-add-usage').classList.contains('v-list-item--disabled')).toBe(true)
    await fireEvent.click(screen.getByTestId('task-form-add-usage'))
    await expect(rubric.value).toHaveLength(1)
  })

  it('改分值、改阈值都写回那一行', async () => {
    const { rubric } = mount([usageLine(20, 10, 100)])

    await fireEvent.update(field('task-form-usage-points'), '30')
    await waitFor(() => expect(rubric.value[0].points).toBe(30))

    await fireEvent.update(field('task-form-usage-full'), '5')
    await waitFor(() => expect(rubric.value[0].config).toMatchObject({ full: 5 }))
  })

  it('试算按后端的阈值算分写在那里', async () => {
    mount([usageLine(20, 0, 100)])

    // 起点是 full 和 zero 中间：50 额度 → 20 × (100 − 50) / 100 = 10。
    expect(screen.getByTestId('task-form-usage-try-score').textContent).toBe('10')

    await fireEvent.update(field('task-form-usage-try'), '80')
    await waitFor(() => expect(screen.getByTestId('task-form-usage-try-score').textContent).toBe('4'))

    // 不超过 full 就是满分。
    await fireEvent.update(field('task-form-usage-try'), '0')
    await waitFor(() => expect(screen.getByTestId('task-form-usage-try-score').textContent).toBe('20'))
  })

  it('合计正好 100 时不报警；少了就当场说清', async () => {
    mount([{ item: '正确性', points: 80, criteria: '照做', stage: null }, usageLine(20)])
    expect(screen.queryByTestId('task-form-auto-total')).toBeNull()

    cleanup()
    mount([{ item: '正确性', points: 70, criteria: '照做', stage: null }, usageLine(20)])
    const warn = await screen.findByTestId('task-form-auto-total')
    expect(warn.textContent).toContain('100')
    expect(warn.textContent).toContain('90')
  })

  it('按排名时不画阈值那两个框，只给一句排名怎么算', async () => {
    mount([usageLine(20)])
    expect(field('task-form-usage-full')).toBeTruthy()

    const rank = screen.getAllByRole('radio').find((b) => b.textContent?.includes('按全班排名'))!
    await fireEvent.click(rank)

    await waitFor(() => expect(screen.queryByTestId('task-form-usage-full')).toBeNull())
    expect(screen.queryByTestId('task-form-usage-try')).toBeNull()
  })
})
