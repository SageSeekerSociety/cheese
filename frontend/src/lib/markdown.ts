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
// The doc editor builds its own instance (it also needs the Tiptap schema) and
// the chat renderer builds its own (it also needs highlighting + KaTeX); both
// apply the same extension. Anything that just parses markdown to HTML uses
// this one instead of marked's global singleton.
import { Marked } from 'marked'
import markedCjkFriendly from 'marked-cjk-friendly'

export const markdown = new Marked(markedCjkFriendly())
