// 窄处读 Word 网页版时盖上去的那一层样式：把「一页纸」拆掉，正文按这一格的宽度重排。
//
// 网页版（后端 `attachment_as_html`，OfficeCLI 出的）照着纸来排：页宽写死成磅数、四周留
// 页边距，再用脚本把整页按比例缩进窗口。390 宽的手机上，这一缩就是正文五六个像素。这里不
// 改它的结构，只拿 !important 把几处「纸」的写法压掉——页宽、页边距、缩放、阴影、分页线——
// 字号、段距、表格、加粗这些原文自己的样子都留着。
//
// 正文行距放到 1.6：Word 的「单倍」写出来是每段内联的 1.15，那是给一行十七八厘米宽的纸用
// 的，手机上一行只有十几个字，挤着读很累。只动段落和列表，标题照原文。
const REFLOW_CSS = `
html, body { background: #fff !important; padding: 0 !important; }
.page-wrapper { width: auto !important; height: auto !important; margin: 0 !important; }
.page {
  width: auto !important; min-height: 0 !important; padding: 16px 16px 24px !important;
  transform: none !important; box-shadow: none !important; border-radius: 0 !important;
  overflow-x: visible !important;
}
.page-body p, .page-body li { line-height: 1.6 !important; }
.page-body p span, .page-body li span { line-height: inherit !important; }
.page::before { border-radius: 0 !important; }
hr.page-break { display: none !important; }
.page-body img, .page-body svg, .page-body canvas { max-width: 100% !important; height: auto !important; }
.page-body table { width: 100% !important; max-width: 100% !important; margin-left: 0 !important; }
.page-body p, .page-body li, .page-body h1, .page-body h2, .page-body h3, .page-body h4 {
  margin-left: 0 !important; margin-right: 0 !important;
}
`

/** 给网页版的 HTML 盖上重排那一层。放在 `</head>` 前面、原文自己的样式之后，同样是
 *  !important 时后写的赢；没有 head 就放最前面。 */
export function reflowDocPage(html: string): string {
  const style = `<style data-cheese-reflow>${REFLOW_CSS}</style>`
  const at = html.search(/<\/head>/i)
  return at >= 0 ? html.slice(0, at) + style + html.slice(at) : style + html
}
