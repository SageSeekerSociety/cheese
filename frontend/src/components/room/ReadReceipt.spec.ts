/** 自己那条消息旁的已读圈：说出是没人读、几个人读了还是全读了；点开分两列列出读了的和
 * 没读的人，人和 AI 队友一样列在里面。 */
import type { Receipt, ReceiptPerson } from '../../lib/readReceipts'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import ReadReceipt from './ReadReceipt.vue'

import { setLocale, t } from '@/i18n'

const zhou: ReceiptPerson = { handle: 'zhou', name: '小周', avatar: null, isAgent: false }
const lin: ReceiptPerson = { handle: 'lin', name: '林老师', avatar: null, isAgent: false }
const cheese: ReceiptPerson = { handle: 'cheese-ab12', name: '小研', avatar: null, isAgent: true }

function mount(receipt: Receipt) {
  return render(ReadReceipt, {
    props: { receipt },
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

beforeEach(() => {
  // The popover is a Vuetify menu: it measures the viewport to place itself.
  vi.stubGlobal('visualViewport', {
    width: 1024,
    height: 768,
    offsetLeft: 0,
    offsetTop: 0,
    addEventListener() {},
    removeEventListener() {},
  })
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  )
  setLocale('zh-CN')
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('ReadReceipt', () => {
  it('says nobody has read it when nobody has', () => {
    mount({ read: [], unread: [zhou, cheese] })
    expect(screen.getByRole('button', { name: t('work.room.readReceipt.unread') })).toBeTruthy()
  })

  it('says how many have read it when some have', () => {
    mount({ read: [zhou], unread: [lin, cheese] })
    expect(screen.getByRole('button', { name: t('work.room.readReceipt.some', { count: 1 }) })).toBeTruthy()
  })

  it('says everyone has read it when everyone has, an AI teammate alone included', () => {
    mount({ read: [cheese], unread: [] })
    expect(screen.getByRole('button', { name: t('work.room.readReceipt.all') })).toBeTruthy()
  })

  it('lists who has read it and who has not, people and AI teammates alike, in roster order', async () => {
    mount({ read: [zhou, cheese], unread: [lin] })
    await fireEvent.click(screen.getByRole('button'))
    const dialog = await screen.findByRole('dialog', { name: t('work.room.readReceipt.dialog') })
    const read = within(dialog).getByRole('region', { name: t('work.room.readReceipt.read') })
    const unread = within(dialog).getByRole('region', { name: t('work.room.readReceipt.unread') })
    const named = (region: HTMLElement, name: string) => within(region).queryByText(name)
    expect(named(read, '小周')).toBeTruthy()
    expect(named(read, '小研')).toBeTruthy()
    expect(named(read, '林老师')).toBeNull()
    expect(named(unread, '林老师')).toBeTruthy()
    expect(named(unread, '小周')).toBeNull()
    // Roster order: 小周 before 小研.
    const order = named(read, '小周')!.compareDocumentPosition(named(read, '小研')!)
    expect(order & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('shows an empty column as empty rather than leaving it out', async () => {
    mount({ read: [], unread: [zhou] })
    await fireEvent.click(screen.getByRole('button'))
    const dialog = await screen.findByRole('dialog')
    const read = within(dialog).getByRole('region', { name: t('work.room.readReceipt.read') })
    expect(within(read).queryAllByRole('listitem')).toEqual([])
    expect(read.textContent).toContain(t('work.room.readReceipt.none'))
  })
})
