import type { Component } from 'vue'
import type { DocAiCard } from '../../../lib/docAiTypes'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render } from '@testing-library/vue'
import { afterEach, expect, it } from 'vitest'

import DocAiPanel from './DocAiPanel.vue'

afterEach(cleanup)

it('keeps a request original and question frozen while the prepared document quote is retired', async () => {
  const card: DocAiCard = {
    request: {
      request_id: 'historic-request',
      kind: 'propose',
      generation: 1,
      state: 'succeeded',
      proposal_id: 'historic-proposal',
      answer: 'historic answer',
      error: null,
    },
    proposal: {
      proposal_id: 'historic-proposal',
      request_id: 'historic-request',
      revision: 1,
      state: 'pending',
      document_id: 'doc',
      base_version: 4,
      selection: { node_id: 'paragraph', start: 0, end: 4, exact_hash: 'a'.repeat(64) },
      replacement: 'historic replacement',
      answer: 'historic answer',
      accepted_by: null,
      accepted_version: null,
    },
    context: {
      state: 'verified',
      question: 'historic question',
      original: 'historic original',
      scope: 'selection',
      baseVersion: 4,
    },
  }
  const { container, rerender } = render(DocAiPanel as Component, {
    props: {
      cards: [card],
      question: 'new draft question',
      busy: false,
      error: '',
      selectionStatus: '',
      hasSelection: true,
      blocked: false,
      unknown: false,
      version: 4,
      docked: true,
      preparedContext: {
        state: 'verified',
        original: 'currently prepared original',
        scope: 'selection',
        baseVersion: 4,
      },
    },
    global: { plugins: [createVuetify({ components, directives })], stubs: { 'v-icon': true } },
  })
  expect(container.textContent).toContain('currently prepared original')
  expect(container.textContent).toContain('historic question')
  expect(container.textContent).toContain('historic original')
  expect(container.textContent).toContain('historic replacement')
  await rerender({ preparedContext: { state: 'unavailable' }, version: 5, hasSelection: false })
  expect(container.textContent).not.toContain('currently prepared original')
  expect(container.textContent).toContain('historic question')
  expect(container.textContent).toContain('historic original')
  expect(container.textContent).toContain('historic replacement')
  expect(container.querySelector('textarea')?.value).toBe('new draft question')
})
