// @vitest-environment jsdom
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useDocPeople } from '@/composables/useDocPeople'

import PanelDoc from './PanelDoc.vue'

import i18n, { setLocale } from '@/i18n'
import { DOC_TOPIC, docSession } from '@/views/demo/catalogFixtures'
import { docBundle, docThreadsBundle } from '@/views/demo/catalogPanelsFixtures'

beforeEach(() => {
  setLocale('zh-CN')
  vi.stubGlobal(
    'visualViewport',
    Object.assign(new EventTarget(), { width: 1024, height: 768, offsetLeft: 0, offsetTop: 0, scale: 1 })
  )
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  )
  vi.stubGlobal('devicePixelRatio', 1)
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
    x: 0,
    y: 0,
    left: 0,
    top: 0,
    width: 1000,
    right: 1000,
    height: 600,
    bottom: 600,
    toJSON: () => ({}),
  })
})
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('document PDF identities', () => {
  it('keeps personal remarks on screen but exports public names through the document shell', async () => {
    const doc = docBundle({ session: docSession('请联系 <@reader>，由 <@cheese-seat> 跟进。') })
    let printed: string | null = null
    doc.savePdf = async (html) => {
      printed = html
    }
    const people = useDocPeople({
      members: () => [
        { user_handle: 'reader', name: 'Public Reader', remark: 'Private colleague', source: 'team' },
        {
          user_handle: 'cheese-seat',
          instance_handle: 'cheese',
          name: 'Public Agent',
          remark: 'Private assistant',
          agent: true,
          source: 'agent',
        },
      ],
      agentHandle: () => 'cheese-seat',
      agentName: () => 'Private assistant',
    })
    render(PanelDoc, {
      props: {
        topic: DOC_TOPIC,
        activityTick: 0,
        agentHandle: 'cheese-seat',
        agentName: 'Private assistant',
        docPanel: doc,
        docThreads: docThreadsBundle(),
        docPeople: people,
      },
      global: { plugins: [createVuetify({ components, directives }), i18n] },
    })
    expect(await screen.findByText('Private colleague')).toBeTruthy()
    await fireEvent.click(screen.getByRole('button', { name: '更多' }))
    await fireEvent.click(await screen.findByRole('menuitem', { name: '导出 PDF' }))
    await waitFor(() => expect(printed).not.toBeNull())
    const exported = new DOMParser().parseFromString(printed!, 'text/html').body.textContent ?? ''
    expect(exported).toContain('Public Reader')
    expect(exported).toContain('Public Agent')
    expect(exported).not.toContain('Private colleague')
    expect(exported).not.toContain('Private assistant')
    expect(screen.getByText('Private colleague')).toBeTruthy()
  })
})
