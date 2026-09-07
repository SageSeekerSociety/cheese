/**
 * Wide content inside rendered markdown must scroll in its own box, never widen
 * the column it sits in.
 *
 * The bug this guards was not a missing rule — it was three copies of the same
 * rule set. `.md-content` is styled in ChatPanel, ProjectDocsView and
 * OverviewView; only the chat pane ever got a table rule, so a table of file
 * paths pushed the project docs and the project summary sideways for as long as
 * the three files existed apart. Adding a fourth copy would have failed the same
 * way on the fifth face, so the rules now live in one stylesheet and the test
 * below is about that arrangement holding: the shared sheet carries the rules,
 * it is actually loaded, and no face quietly grows its own copy again.
 *
 * A layout assertion (does the table actually scroll?) cannot live here —
 * happy-dom has no layout engine, so `scrollWidth` is 0 for everything and a
 * test written against it would pass whatever the CSS said. That measurement was
 * made in a real browser instead; this file protects the structure that
 * measurement depends on.
 */
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

import mainTs from '../main.ts?raw'

// Read off disk rather than imported: vitest runs with CSS processing off, so
// `?raw` on a stylesheet arrives as an empty string — in either the plain or the
// glob form — and every assertion below would pass against nothing.
//
// The path is resolved through `fileURLToPath`, which takes the URL as a string:
// happy-dom installs its own global `URL`, and node's fs rejects instances of it
// with "The URL must be of scheme file".
const HERE = dirname(fileURLToPath(import.meta.url))
const shared = readFileSync(join(HERE, 'md-content.css'), 'utf8')

// Every component that renders markdown into a `.md-content` element.
const VUE_SOURCES = import.meta.glob('../**/*.vue', {
  query: '?raw',
  eager: true,
  import: 'default',
}) as Record<string, string>

/** Properties that decide whether wide content escapes its column. */
const CONTAINMENT = ['overflow-x', 'overflow-wrap', 'max-width', 'width']

/** The `.md-content` rule bodies a component declares for itself. */
function mdContentRules(source: string): string {
  let out = ''
  const re = /\.md-content[^{}]*\{([^}]*)\}/g
  for (const m of source.matchAll(re)) out += m[1] + '\n'
  return out
}

function facesRenderingMdContent(): [string, string][] {
  return Object.entries(VUE_SOURCES).filter(([, src]) => /class="[^"]*\bmd-content\b/.test(src))
}

describe('.md-content 的溢出规则', () => {
  it('都写在共享样式里', () => {
    // Each of these is a way content gets wider than its column: a table that
    // cannot wrap, a code block with a long line, an oversized image, a bare URL.
    expect(shared).toMatch(/\.md-content table\s*\{[^}]*overflow-x:\s*auto/)
    expect(shared).toMatch(/\.md-content table\s*\{[^}]*max-width:\s*100%/)
    expect(shared).toMatch(/\.md-content pre\s*\{[^}]*overflow-x:\s*auto/)
    expect(shared).toMatch(/\.md-content img\s*\{[^}]*max-width:\s*100%/)
    expect(shared).toMatch(/\.md-content a\s*\{[^}]*overflow-wrap:\s*anywhere/)
  })

  it('那份共享样式真的被加载', () => {
    // Without this import the stylesheet is dead code and every face regresses
    // silently — the rules would still be in the tree, just never applied.
    expect(mainTs).toMatch(/import ['"]@\/styles\/md-content\.css['"]/)
  })

  it('没有哪个面自己再留一份', () => {
    const faces = facesRenderingMdContent()
    // If this ever finds nothing, the glob or the class name moved and the rest
    // of this test would be vacuously true.
    expect(faces.length).toBeGreaterThanOrEqual(3)

    for (const [file, source] of faces) {
      const declared = mdContentRules(source)
      for (const prop of CONTAINMENT) {
        expect(
          declared,
          `${file} 又给 .md-content 写了一份 ${prop}；这类规则归 styles/md-content.css 管，` +
            `分开写就是这个 bug 本来的样子`
        ).not.toMatch(new RegExp(`(^|[;{\\s])${prop}\\s*:`))
      }
    }
  })
})
