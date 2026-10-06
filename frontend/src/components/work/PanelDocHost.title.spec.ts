// @vitest-environment jsdom
// A library document is named on its own page: the title typed there is saved
// as its name. On dev (2026-10-05) a title typed right after creating a
// document, while the page was still finishing its load, was never sent.
import type { Component } from 'vue'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { resetRooms, seedRoom } from '../../test/fakeDocCollab'

const mocks = vi.hoisted(() => ({
  renameDocument: vi.fn(),
  getDocumentAbout: vi.fn(),
}))

vi.mock('../../api/docThreads', () => ({
  listDocThreads: async () => ({ data: [], total: 0 }),
  writeDocThread: async () => ({}),
}))
vi.mock('../../api/docHistory', () => ({
  getDocVersions: async () => ({ versions: [], cursor: null }),
  restoreDocVersion: async () => ({}),
}))
vi.mock('../../api/projectDocuments', async () => ({
  ...(await vi.importActual<typeof import('../../api/projectDocuments')>('../../api/projectDocuments')),
  renameDocument: (...a: unknown[]) => mocks.renameDocument(...a),
  getDocumentAbout: (...a: unknown[]) => mocks.getDocumentAbout(...a),
}))
vi.mock('../../composables/useDocCollab', async () => ({
  useDocCollab: (await import('../../test/fakeDocCollab')).useFakeDocCollab,
}))

import PanelDocHost from './PanelDocHost.vue'

import { setLocale, t } from '@/i18n'

const Doc = PanelDocHost as unknown as Component

beforeEach(() => {
  setLocale('zh-CN')
  resetRooms()
  mocks.renameDocument.mockReset()
  mocks.getDocumentAbout.mockReset()
  mocks.renameDocument.mockImplementation(async (_id: string, title: string) => ({ title }))
})
afterEach(cleanup)

describe('a new library document', () => {
  it('is named what was typed as its title, however soon it is typed', async () => {
    seedRoom('doc-1', '')
    // The page asks what the new document is called; the answer arrives late.
    let answer: (about: { title: string }) => void = () => {}
    mocks.getDocumentAbout.mockReturnValue(new Promise((resolve) => (answer = resolve)))
    render(Doc, {
      props: {
        topic: null,
        document: { id: 'doc-1', projectId: 'p1', title: '' },
        activityTick: 0,
        topicList: [],
      },
      global: { plugins: [createVuetify({ components, directives })] },
    })
    const box = (await screen.findByRole('textbox', {
      name: t('work.room.doc.titleLabel'),
    })) as HTMLInputElement

    await fireEvent.update(box, '定价对比')
    await fireEvent.keyDown(box, { key: 'Enter' })
    await fireEvent.blur(box)
    answer({ title: '' })

    await waitFor(() => expect(mocks.renameDocument).toHaveBeenCalledWith('doc-1', '定价对比'))
    await waitFor(() => expect(box.value).toBe('定价对比'))
  })

  it('shows a name given elsewhere while nobody is typing in it', async () => {
    seedRoom('doc-1', '')
    mocks.getDocumentAbout.mockResolvedValue({ title: '' })
    const view = render(Doc, {
      props: {
        topic: null,
        document: { id: 'doc-1', projectId: 'p1', title: '' },
        activityTick: 0,
        topicList: [],
      },
      global: { plugins: [createVuetify({ components, directives })] },
    })
    const box = (await screen.findByRole('textbox', {
      name: t('work.room.doc.titleLabel'),
    })) as HTMLInputElement

    await view.rerender({ document: { id: 'doc-1', projectId: 'p1', title: '竞品定价' } })

    await waitFor(() => expect(box.value).toBe('竞品定价'))
  })
})
