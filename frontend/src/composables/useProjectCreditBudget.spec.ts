import { defineComponent, h, nextTick, ref } from 'vue'
import { cleanup, render } from '@testing-library/vue'
import { afterEach, expect, it, vi } from 'vitest'

const calls = vi.hoisted(() => ({ get: vi.fn(), save: vi.fn() }))
vi.mock('@/api', () => ({
  getProjectCreditBudget: calls.get,
  saveProjectCreditBudget: calls.save,
}))

import { useProjectCreditBudget } from './useProjectCreditBudget'

afterEach(() => {
  cleanup()
  vi.resetAllMocks()
})

it('a save for the previous project cannot replace the new project budget', async () => {
  const project = ref('first')
  let finish!: (value: unknown) => void
  calls.get.mockImplementation(async (id: string) => ({ credits_limit: id === 'first' ? 10 : 20 }))
  calls.save.mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = resolve
      })
  )
  let state!: ReturnType<typeof useProjectCreditBudget>
  render(
    defineComponent({
      setup() {
        state = useProjectCreditBudget(() => project.value)
        return () => h('div')
      },
    })
  )
  await nextTick()
  const pending = state.save({ credits_limit: 50 })
  project.value = 'second'
  await nextTick()
  await nextTick()
  finish({ credits_limit: 50 })
  await pending
  expect(state.budget.value?.credits_limit).toBe(20)
  expect(state.saving.value).toBe(false)
})
