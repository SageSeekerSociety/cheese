// 逐文件 diff：把 `git diff` 的一整块文本切成「一个文件一段」，并给每一行定性。
//
// 改动 tab 以前是两个半成品并排：一坨没有语法着色、不能按文件跳的裸 <pre>，和一棵
// 不知道哪些文件被改过的树。要把它们合成一个能验收的面，缺的就是这一层——树上标
// 什么、点开看哪一段，都是从这里读出来的。
//
// 它是纯函数并且住在 lib/ 里，因为「哪些行算新增」这种判断出错的方式是安静的：
// 面板照样渲染，只是标错。

export interface FileDiff {
  /** Path as the reviewer knows it — the b-side, or the a-side for a deletion. */
  path: string
  /** Lines added / removed, for the marker on the tree row. */
  added: number
  removed: number
  /** Whether this file was created or deleted by the topic's branch. */
  status: 'added' | 'removed' | 'modified'
  /** The file's own hunk text, header included. */
  body: string
}

export type DiffLineKind = 'add' | 'del' | 'hunk' | 'meta' | 'context'

export interface DiffLine {
  kind: DiffLineKind
  text: string
}

/** A line plus the old/new line numbers the gutter shows. `null` on a side the
 * line does not exist on (an addition has no old number), and on the header
 * lines that address no line at all. */
export interface DiffRow extends DiffLine {
  oldNumber: number | null
  newNumber: number | null
}

/** How many diff lines the panel renders before it stops and offers the rest.
 * A whole-worktree diff can run to tens of thousands of lines; laying every one
 * out as its own element is what makes the tab hang. */
export const DIFF_WINDOW = 400

/** `@@ -oldStart[,oldCount] +newStart[,newCount] @@ optional context`. */
const HUNK_HEADER = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/

/** Header lines git writes between the `diff --git` line and the first `@@`.
 * They describe the change (mode, rename, binary) but are not content, so the
 * gutter must leave them blank — rendered as context they showed up as "0 0". */
const META_HEADER =
  /^(old mode |new mode |Binary files |GIT binary patch|copy from |copy to |rename from |rename to |dissimilarity index )/

/**
 * Walk one file's hunk text and carry the two running counters the gutter needs.
 * A diff line does not carry its own number: `@@` says where each side starts,
 * and every context/add/del line moves the matching counter one step. Getting
 * this wrong shifts every number after the mistake, silently — so it is a pure
 * function, tested against a real `git diff`.
 */
export function numberDiffLines(lines: DiffLine[]): DiffRow[] {
  let oldNo = 0
  let newNo = 0
  return lines.map((line) => {
    if (line.kind === 'hunk') {
      const m = HUNK_HEADER.exec(line.text)
      oldNo = m ? Number.parseInt(m[1] ?? '0', 10) : 0
      newNo = m ? Number.parseInt(m[2] ?? '0', 10) : 0
      return { ...line, oldNumber: null, newNumber: null }
    }
    if (line.kind === 'meta') return { ...line, oldNumber: null, newNumber: null }
    if (line.kind === 'add') {
      const row = { ...line, oldNumber: null, newNumber: newNo }
      newNo += 1
      return row
    }
    if (line.kind === 'del') {
      const row = { ...line, oldNumber: oldNo, newNumber: null }
      oldNo += 1
      return row
    }
    // A context line exists on both sides, so it advances both counters.
    const row = { ...line, oldNumber: oldNo, newNumber: newNo }
    oldNo += 1
    newNo += 1
    return row
  })
}

/** Unquote git's C-style path escaping (`"a\tb"`), which it uses for paths with
 * spaces or non-ASCII bytes. Left as-is when the path is not quoted. */
function unquote(path: string): string {
  if (!path.startsWith('"') || !path.endsWith('"')) return path
  const escapes: Record<string, string> = {
    '\\': '\\',
    '"': '"',
    a: '\x07',
    b: '\b',
    f: '\f',
    n: '\n',
    r: '\r',
    t: '\t',
    v: '\v',
  }
  return path.slice(1, -1).replace(/(?:\\[0-7]{3})+|\\([\\"abfnrtv])/g, (value, escape: string) => {
    if (escape) return escapes[escape]!
    const bytes = value.match(/[0-7]{3}/g)!.map((octal: string) => Number.parseInt(octal, 8))
    return new TextDecoder().decode(new Uint8Array(bytes))
  })
}

/** The path a `diff --git a/X b/X` header is about. */
function headerPath(header: string): string | null {
  const body = header.slice('diff --git '.length)
  // git quotes both sides when the path needs escaping. Read the b-side: it is
  // the name the reviewer knows, and it is present even for a deletion (only
  // the `+++ /dev/null` line marks that).
  if (body.startsWith('"')) {
    const quoted = body.match(/^"a\/(.*)" "b\/(.*)"$/)
    return quoted ? unquote(`"${quoted[2]}"`) : null
  }
  // Unquoted: split on the LAST " b/", so a path containing " b/" itself does
  // not truncate the name.
  const cut = body.lastIndexOf(' b/')
  return cut < 0 ? null : body.slice(cut + 3)
}

/**
 * Split a whole-branch diff into one entry per file, in the order git emitted.
 *
 * Anything before the first `diff --git` header (git emits none for an empty
 * diff) is dropped: it is not about a file, and the panel addresses files.
 */
export function splitDiffByFile(diff: string): FileDiff[] {
  const out: FileDiff[] = []
  let current: { path: string; lines: string[] } | null = null

  const flush = () => {
    if (!current) return
    const body = current.lines.join('\n')
    let added = 0
    let removed = 0
    let status: FileDiff['status'] = 'modified'
    for (const line of current.lines) {
      if (line.startsWith('new file mode')) status = 'added'
      else if (line.startsWith('deleted file mode')) status = 'removed'
      // `+++`/`---` are the file headers, not content.
      else if (line.startsWith('+') && !line.startsWith('+++')) added += 1
      else if (line.startsWith('-') && !line.startsWith('---')) removed += 1
    }
    out.push({ path: current.path, added, removed, status, body })
    current = null
  }

  for (const line of diff.split('\n')) {
    if (line.startsWith('diff --git ')) {
      flush()
      const path = headerPath(line)
      // A header we cannot read is not a reason to lose the rest of the diff;
      // it becomes its own entry under the raw header so nothing is silently
      // dropped from a review surface.
      current = { path: path ?? line.slice('diff --git '.length), lines: [line] }
      continue
    }
    if (current) current.lines.push(line)
  }
  flush()
  return out
}

/** Classify each line of a hunk so the renderer can colour it. */
export function parseDiffLines(body: string): DiffLine[] {
  return body.split('\n').map((text) => {
    if (text.startsWith('@@')) return { kind: 'hunk' as const, text }
    if (text.startsWith('+++') || text.startsWith('---') || text.startsWith('diff --git ')) {
      return { kind: 'meta' as const, text }
    }
    if (text.startsWith('index ') || text.startsWith('new file mode') || text.startsWith('deleted file mode')) {
      return { kind: 'meta' as const, text }
    }
    if (text.startsWith('similarity index') || text.startsWith('rename ')) return { kind: 'meta' as const, text }
    // A line that is content on neither side: git's "\ No newline at end of
    // file" marker — it annotates the line above, so it is not a line itself —
    // and the mode/binary headers. As "context" they advanced both counters,
    // shifting every number after them, and the gutter showed them as "0 0".
    if (text.startsWith('\\') || META_HEADER.test(text)) return { kind: 'meta' as const, text }
    if (text.startsWith('+')) return { kind: 'add' as const, text }
    if (text.startsWith('-')) return { kind: 'del' as const, text }
    return { kind: 'context' as const, text }
  })
}
