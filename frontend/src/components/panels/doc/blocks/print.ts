// A document as a page of paper.
//
// The printout is made from the same reading copy the document is shown in
// (./reader.ts), so the blocks a reader sees are the blocks the page has. The
// page is then handed to the platform's printing browser
// (`deploy/browser-render`), which turns it into an A4 PDF — the same browser
// `cheese check --pdf` prints with. That browser is the reason the page has to
// carry everything it needs by itself: the app's stylesheets, copied in, and the
// pictures, whose bytes come in through `image` because the printing browser
// has no credential.
//
// Two blocks are not paper, and reader.ts/the step below say so instead of
// printing an empty frame: a mini-app is named, a video keeps its address.
import type { RefNames } from '../../../../lib/refChip'

import { mountMarkdown } from './reader'

import { t } from '@/i18n'

export interface PrintOptions {
  /** 标题，印在第一页最上面。 */
  title: string
  /** 文档现在的正文（与导出 Markdown 的同一份）。 */
  markdown: string
  names?: RefNames
  /** 文档里引用的一张图用什么地址画：**自带字节的地址**（data: URL），或者一个别人
   *  也能取的公开网址。取不到时给空串，正文里的说明文字代替它。 */
  image?: (src: string) => Promise<string>
}

/** A4's width at 96 dpi, less the 18mm the printer leaves on each side: the
 *  column this copy is laid out in is the column the page will have. */
const COLUMN_PX = 658

/** Past this the printing browser spends longer than anyone waits. The page is
 *  mostly its pictures, so a document with several photos can reach it. */
const MAX_PAGE_CHARS = 18 * 1024 * 1024

/** Markdown and a title as one self-contained HTML page. */
export async function documentPrintHtml(opts: PrintOptions): Promise<string> {
  const host = document.createElement('div')
  // Laid out where it can be measured — a chart sizes itself from the column it
  // is in — and out of sight, so copying does not show.
  host.className = 'doc-print-source'
  host.style.cssText = `position:fixed;left:0;top:0;width:${COLUMN_PX}px;opacity:0;pointer-events:none;z-index:-1`
  document.body.append(host)
  try {
    const stop = mountMarkdown(host, opts.markdown, { names: opts.names, eager: true, print: true })
    try {
      await drawImages(host, opts.image)
      await settled(host)
      keepVideoAddresses(host)
      const page = printablePage(opts.title, host.innerHTML)
      if (page.length > MAX_PAGE_CHARS) throw new Error(t('work.room.doc.exportPdfTooLarge'))
      return page
    } finally {
      stop()
    }
  } finally {
    host.remove()
  }
}

/** Each picture drawn from the address the caller resolved, in the copy itself. */
async function drawImages(host: HTMLElement, load: PrintOptions['image']): Promise<void> {
  await Promise.all(
    Array.from(host.querySelectorAll('img')).map(async (img) => {
      const src = img.getAttribute('src') ?? ''
      if (!src || !load) return
      const shown = await load(src).catch(() => '')
      // Not readable: what the picture says is its alt text, as in the editor.
      if (shown) img.setAttribute('src', shown)
      else img.removeAttribute('src')
    })
  )
}

/** Wait until the blocks that draw themselves later have drawn: charts,
 *  diagrams, formulas. One that never finishes is not waited for forever — a
 *  page as it stands beats no page. */
async function settled(host: HTMLElement, timeoutMs = 4000): Promise<void> {
  const drawn = () =>
    Array.from(host.querySelectorAll('[data-block="chart"] .doc-chart__canvas')).every(
      (el) => !!el.querySelector('svg')
    ) &&
    Array.from(host.querySelectorAll('[data-block="mermaid"]')).every(
      (el) =>
        !!el.querySelector('svg') ||
        el.classList.contains('doc-mermaid--error') ||
        el.classList.contains('doc-mermaid--empty')
    ) &&
    Array.from(host.querySelectorAll('[data-math]')).every(
      (el) => !(el.textContent ?? '').trim() || !!el.querySelector('.katex')
    )
  const deadline = performance.now() + timeoutMs
  while (!drawn() && performance.now() < deadline) await new Promise((wait) => setTimeout(wait, 50))
  await document.fonts?.ready
}

/** A video player is a frame of the internet, not a picture: on paper it stays
 *  the address, which still opens it. */
function keepVideoAddresses(host: HTMLElement): void {
  for (const block of Array.from(host.querySelectorAll('[data-video]'))) {
    const frame = block.querySelector('iframe')
    if (!frame) continue
    const link = document.createElement('a')
    link.href = block.getAttribute('data-video') ?? frame.getAttribute('src') ?? ''
    link.textContent = link.href
    frame.replaceWith(link)
  }
}

const URL_IN_CSS = /url\((["']?)([^"')]+)\1\)/g

/** Every rule the page is drawn with, so the copy needs nothing from the app to
 *  look like the app. A sheet another origin will not let us read is left out;
 *  an address inside a rule is made absolute, because the copy has no address
 *  of its own to resolve it against. */
function styleSheets(): string {
  const rules: string[] = []
  for (const sheet of Array.from(document.styleSheets)) {
    let list: CSSRuleList
    try {
      list = sheet.cssRules
    } catch {
      continue
    }
    const base = sheet.href ?? location.href
    for (const rule of Array.from(list)) {
      rules.push(
        rule.cssText.replace(URL_IN_CSS, (whole, quote: string, url: string) =>
          /^(?:[a-z][a-z0-9+.-]*:|\/\/|#)/i.test(url) ? whole : `url(${quote}${new URL(url, base).href}${quote})`
        )
      )
    }
  }
  return rules.join('\n')
}

/** What paper needs that a screen does not. */
const PAGE_CSS = `
@page { size: A4; margin: 16mm 18mm; }
html, body { margin: 0; padding: 0; background: #fff !important; }
body { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
.doc-print { max-width: none; margin: 0; }
.doc-print__title { margin: 0 0 14px; font-size: 25px; line-height: 1.35; font-weight: 700; }
.doc-print__body > * { content-visibility: visible !important; }
.doc-print .md-pre-bar, .doc-print .md-code-btn, .doc-print .doc-details__toggle,
.doc-print .doc-chart__bar button, .doc-print .doc-app__head, .doc-print .doc-app__stage { display: none !important; }
.doc-print [data-block="details"] [data-field="details"] {
  height: auto !important; margin-top: 8px !important; opacity: 1 !important; content-visibility: visible !important;
}
.doc-print :is(table, pre, blockquote, .doc-callout, .doc-stats, .doc-timeline, figure,
  [data-block="chart"], [data-block="mermaid"], [data-block="app"]) { break-inside: avoid; }
.doc-print tr, .doc-print .doc-stat, .doc-print .doc-tl__item { break-inside: avoid; }
.doc-print :is(h1, h2, h3, h4, h5, h6) { break-after: avoid; }
.doc-print .tableWrapper { overflow: visible !important; }
.doc-print :is(td, th) { overflow-wrap: anywhere; }
.doc-print pre { white-space: pre-wrap; overflow-wrap: anywhere; }
`

function printablePage(title: string, body: string): string {
  return `<!doctype html>
<html lang="${escapeHtml(document.documentElement.lang || 'zh-CN')}">
<head>
<meta charset="utf-8">
<title>${escapeHtml(title)}</title>
<style>${styleSheets()}</style>
<style>${PAGE_CSS}</style>
</head>
<body>
<main class="doc-print">
<h1 class="doc-print__title">${escapeHtml(title)}</h1>
<div class="doc-reading doc-print__body">${body}</div>
</main>
</body>
</html>`
}

function escapeHtml(text: string): string {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')
}
