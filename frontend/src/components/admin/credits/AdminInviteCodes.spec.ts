// 管理员的邀请码一览：每个人自己的那张默认不列（它们由各人管、数量远多于平台发的码），
// 要看时再打开；停用会先问一句，确认了才停。
import type { AdminInviteCode } from '@/types/invites'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import AdminInviteCodes from './AdminInviteCodes.vue'

import i18n, { setLocale, t } from '@/i18n'

const code = (id: number, value: string, note: string | null, isActive = true): AdminInviteCode => ({
  id,
  code: value,
  maxUses: 5,
  useCount: 1,
  isActive,
  createdBy: 1,
  note,
  createdAt: '2026-10-01T00:00:00Z',
  expiresAt: null,
})

const CODES = [code(1, 'PLATFORM1', 'workshop'), code(2, 'PERSONAL2', 'personal')]

beforeEach(() => {
  setLocale('en')
  vi.stubGlobal('visualViewport', new EventTarget())
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function shown(codes: AdminInviteCode[] = CODES) {
  return render(AdminInviteCodes, {
    props: { codes, error: null, busy: false, actionError: null },
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
}

describe('admin invite codes', () => {
  it('lists personal codes only when asked to', async () => {
    const view = shown()
    expect(view.queryByText('PLATFORM1')).not.toBeNull()
    expect(view.queryByText('PERSONAL2')).toBeNull()

    // Vuetify's switch reads the input event, as a person's click produces it.
    await fireEvent.input(view.getByLabelText(t('credits.inviteCodes.showPersonal', { n: 1 })), {
      target: { checked: true },
    })

    await view.findByText('PERSONAL2')
  })

  it('deactivates a code only after the confirmation', async () => {
    const view = shown()
    await fireEvent.click(view.getByRole('button', { name: t('credits.inviteCodes.deactivate') }))
    expect(view.emitted('deactivate')).toBeUndefined()

    const dialog = await view.findByRole('alertdialog')
    const confirm = [...dialog.querySelectorAll('button')].find(
      (button) => button.textContent?.trim() === t('credits.inviteCodes.deactivate')
    )!
    await fireEvent.click(confirm)

    await waitFor(() => expect(view.emitted('deactivate')).toEqual([[1]]))
  })

  it('creates a code with the uses and note given', async () => {
    const view = shown()
    await fireEvent.update(view.getByLabelText(t('credits.inviteCodes.maxUses')), '20')
    await fireEvent.update(view.getByLabelText(t('credits.inviteCodes.note')), 'Open day')
    // happy-dom does not submit a form from its submit button; the form's own submit is what a click sends.
    await fireEvent.submit(view.getByRole('button', { name: t('credits.inviteCodes.create') }).closest('form')!)

    expect(view.emitted('create')).toEqual([[20, 'Open day']])
  })
})
