// The app's read-only markdown parser.
//
// It exists to be CJK-friendly: CommonMark's flanking rules say a closing `**`
// preceded by punctuation and followed by a letter cannot close, so
// `**执行档案（ExecutionProfile）**解析` and `**这句。**下一句` render as literal
// asterisks. English never trips it — a space always follows the delimiter —
// and Chinese trips it constantly. `marked-cjk-friendly` (CommonMark issue
// #650) counts CJK characters as punctuation for flanking, restoring the
// escape hatch without touching non-CJK text.
//
// The doc editor builds its own instance (it also needs the Tiptap schema);
// everything else — including the chat renderer — parses through this one
// rather than marked's global singleton, so the CJK extension is never the
// thing someone forgot to apply.
//
// Note what that means today: chat code blocks are NOT highlighted and math is
// NOT rendered, in either surface. The doc editor carries hljs styles; the chat
// carries neither. Same markdown, two renderings — worth closing, and worth not
// describing as already closed.
import DOMPurify from 'dompurify'
import { Marked } from 'marked'
import markedCjkFriendly from 'marked-cjk-friendly'

export const markdown = new Marked(markedCjkFriendly())

// 链接一律新开一页。**这个应用里的每一处 markdown 都长在一个你正待着的地方**
// —— 一个房间、一份文档、一张卡。当前标签页跳走就是离开它:这是单页应用,回来
// 要整个重载,输入框里没发出去的字也没了。说明书的链接是最常被点的那种,但对 PR
// 链接和外部资料同样成立。
//
// 放在解析器上而不是某个渲染函数里:所有 markdown 出口都过这一个实例,所以将来
// 新加的渲染路径自动就是对的,不用记得再补一次。(文档编辑器自建实例,它那边早
// 就在 docMarkdown.ts 里加了同样的 target。)
markdown.use({
  renderer: {
    link({ href, title, tokens }) {
      const text = this.parser.parseInline(tokens)
      const titleAttr = title ? ` title="${title}"` : ''
      // noopener: 被打开的那一页拿不到 window.opener,改不了我们这一页。
      return `<a href="${href}"${titleAttr} target="_blank" rel="noopener noreferrer">${text}</a>`
    },
  },
})

// 渲染出来的 HTML 都从这里过一次。**DOMPurify 默认会把 `target` 剥掉** —— 上面
// 那个 renderer 写出来的属性活不到页面上,而且是无声的:链接照常显示、照常能点,
// 只是又变回了当前页跳转。所以放行它这件事必须和产生它的地方摆在一起,不然下一
// 个人只会看到一半的规则。
export function sanitizeRendered(html: string): string {
  return DOMPurify.sanitize(html, { ADD_ATTR: ['target'] })
}
