import type { ProjectCreditBudget as Budget } from '@/types/creditBudgets'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import ProjectCreditBudget from './ProjectCreditBudget.vue'

import { setLocale } from '@/i18n'

const budget: Budget = {
  credits_limit: 1000,
  credits_used: 30,
  credits_remaining: 970,
  public_credits: 10,
  unattributed_credits: 0,
  members: { alice: { name: 'Alice', credits_limit: 200, credits_used: 20 } },
  since: '2026-10-01T00:00:00+00:00',
}

function mount(editable = true) {
  const onSave = vi.fn()
  render(ProjectCreditBudget, {
    props: { budget, editable, onSave },
    global: { plugins: [createVuetify({ components, directives })] },
  })
  return onSave
}

beforeEach(() => setLocale('en'))
afterEach(cleanup)

it('submits only the member limit that changed', async () => {
  const onSave = mount()
  await fireEvent.update(screen.getByRole('spinbutton', { name: 'Task credit limit for Alice' }), '250')
  const button = screen.getByRole('button', { name: 'Save budgets' })
  expect(button.getAttribute('type')).toBe('submit')
  await fireEvent.submit(button.closest('form')!)
  expect(onSave).toHaveBeenCalledWith({ member_limits: { alice: 250 } })
})

it('clearing a limit removes it without issuing credits', async () => {
  const onSave = mount()
  await fireEvent.update(screen.getByRole('spinbutton', { name: 'Task credit limit for Alice' }), '')
  await fireEvent.submit(screen.getByRole('button', { name: 'Save budgets' }).closest('form')!)
  expect(onSave).toHaveBeenCalledWith({ member_limits: { alice: null } })
})

it('does not submit a negative limit', async () => {
  const onSave = mount()
  await fireEvent.update(screen.getByRole('spinbutton', { name: 'Task credit limit for Alice' }), '-1')
  expect((screen.getByRole('button', { name: 'Save budgets' }) as HTMLButtonElement).disabled).toBe(true)
  expect(onSave).not.toHaveBeenCalled()
})

it('a reader cannot edit or submit budgets', () => {
  const onSave = mount(false)
  expect(screen.queryAllByRole('spinbutton')).toHaveLength(0)
  expect(screen.queryByRole('button', { name: 'Save budgets' })).toBeNull()
  expect(onSave).not.toHaveBeenCalled()
})
