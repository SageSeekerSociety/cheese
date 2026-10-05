// The view LegalDocument.vue renders: one published version's meta line and its
// own markdown, drawn from the document the page hands it.
import { cleanup, render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import LegalDocumentView from './LegalDocumentView.vue'

import { setLocale } from '@/i18n'

// The markdown body pulls in the whole reading stack; what this view owns is
// handing the reader the right source, so the stub stands in for it.
vi.mock('@/components/common/MarkdownView.vue', () => ({
  default: { props: ['source'], template: '<div class="md-stub">{{ source }}</div>' },
}))

beforeEach(() => setLocale('en'))
afterEach(cleanup)

const doc = {
  document: 'terms' as const,
  title: 'Terms of Service',
  version: '2.0',
  effectiveDate: '2026-01-01',
  current: true,
  content: '# Terms\n\nBe nice to each other.',
  sha256: 'abc',
}

function mount(props: { doc?: typeof doc | null; error?: string } = {}) {
  return render(LegalDocumentView, {
    props: { doc, error: '', ...props },
  })
}

describe('LegalDocumentView', () => {
  it('draws the version, its effective date and its markdown', () => {
    const view = mount()
    expect(view.getByText(/Version 2\.0/)).toBeTruthy()
    expect(view.getByText(/Effective 2026-01-01/)).toBeTruthy()
    expect(view.getByText(/Be nice to each other\./)).toBeTruthy()
  })

  it('shows the sentence it was handed when the version could not be read', () => {
    const view = mount({ doc: null, error: 'This page could not be loaded. Refresh and try again' })
    expect(view.getByText('This page could not be loaded. Refresh and try again')).toBeTruthy()
    expect(view.queryByText(/Version/)).toBeNull()
  })
})
