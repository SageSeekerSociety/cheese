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
import { Marked } from 'marked'
import markedCjkFriendly from 'marked-cjk-friendly'

export const markdown = new Marked(markedCjkFriendly())
