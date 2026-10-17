// The writing guides (documents, and a topic's living document) show 芝士
// how to spell every block. Each sample in it must
// be accepted by the write check and come back from the document byte for
// byte: an edit quotes the document's text exactly, so a sample the document
// respells teaches a spelling no edit can find.
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { describe, expect, it } from 'vitest'

import { nodeMarkdown, parseMarkdown } from '../src/lib/docSchema'

import { checkMarkdownWrite } from './writeCheck'

const LIBRARY = resolve(__dirname, '../../backend/app/domain/skill_library')
const GUIDE = ['doc_writing.md', 'doc_blocks.md', 'doc_form.md']
  .map((name) => readFileSync(resolve(LIBRARY, name), 'utf8'))
  .join('\n')

/** Every fenced sample: a plain fence holds Markdown; a fence with a language (mermaid, python) is itself the sample, a code block in the document. */
const samples = [...GUIDE.matchAll(/^```(\w*)\n([\s\S]*?)^```$/gm)].map(([whole, lang, body]) =>
  lang ? whole : body.replace(/\n$/, '')
)

describe('the samples in the writing guide', () => {
  it('are there to check', () => {
    expect(samples.length).toBeGreaterThan(5)
  })

  it.each(samples)('are accepted and kept as written: %s', (sample) => {
    expect(checkMarkdownWrite(sample)).toBeNull()
    expect(nodeMarkdown(parseMarkdown(sample))).toBe(sample)
  })
})
