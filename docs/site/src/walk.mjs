// The walk 问芝士 took to the answer: what it searched for, which page it read.
//
// The backend streams a `tool` event for every tool call the model makes. Those
// become the lines the reader watches while the answer is being found, and one
// folded line once it is — so a reader who only wants the answer is not made to
// scroll past the way there.
//
// Kept here, apart from app.js, because it is the part worth testing on its own:
// the panel's markup is otherwise reachable only through a browser, a signed-in
// session and a live model.
import { ic } from './content.js'

// One `tool` event, as something a reader can read.
export function stepOf(event) {
  if (event?.kind === 'search') return { icon: 'search', text: `正在搜：${event.query}` }
  if (event?.kind === 'fetch') return { icon: 'doc', text: `正在读：${event.title}` }
  return { icon: 'list', text: '正在看有哪些页' }
}

// All of them, open while the answer is still being found and folded up after.
// `esc` is the page's escaper: a query is whatever the reader typed, and a
// page title comes from a file someone else wrote.
export function walkHtml(steps, live, esc) {
  if (!steps.length) return ''
  const rows = steps
    .map((s, n) => `<div class="walk-item${live && n === steps.length - 1 ? ' live' : ''}">${ic(s.icon)}<span>${esc(s.text)}</span></div>`)
    .join('')
  const head = live ? '正在查文档' : `查了 ${steps.length} 步`
  return `<details class="walk"${live ? ' open' : ''}><summary>${head}</summary>${rows}</details>`
}
