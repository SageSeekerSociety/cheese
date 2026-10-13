import { describe, expect, it } from 'vitest'

import { DIFF_WINDOW, hunkLabel, numberDiffLines, parseDiffLines, splitDiffByFile } from './diff'

const DIFF = `diff --git a/src/a.py b/src/a.py
index 1111111..2222222 100644
--- a/src/a.py
+++ b/src/a.py
@@ -1,3 +1,4 @@
 keep
-old line
+new line
+another new line
diff --git a/docs/new.md b/docs/new.md
new file mode 100644
index 0000000..3333333
--- /dev/null
+++ b/docs/new.md
@@ -0,0 +1,2 @@
+# 标题
+正文
diff --git a/gone.txt b/gone.txt
deleted file mode 100644
index 4444444..0000000
--- a/gone.txt
+++ /dev/null
@@ -1,1 +0,0 @@
-再见
`

describe('逐文件 diff', () => {
  it('按文件切开，顺序照 git 给的来', () => {
    expect(splitDiffByFile(DIFF).map((f) => f.path)).toEqual(['src/a.py', 'docs/new.md', 'gone.txt'])
  })

  // 树上那个 +2 −1 就是这里数出来的，数错了不会报错，只会标错。
  it('数增删行，不把 +++ / --- 这两行文件头算进去', () => {
    const [a] = splitDiffByFile(DIFF)
    expect({ added: a.added, removed: a.removed }).toEqual({ added: 2, removed: 1 })
  })

  it('新增和删除的文件认得出来——树上要标的不是「改了」', () => {
    const [, added, removed] = splitDiffByFile(DIFF)
    expect(added.status).toBe('added')
    expect(removed.status).toBe('removed')
    expect(splitDiffByFile(DIFF)[0].status).toBe('modified')
  })

  it('每一段自带完整的 hunk 文本，点开文件看的就是它', () => {
    const [a] = splitDiffByFile(DIFF)
    expect(a.body).toContain('@@ -1,3 +1,4 @@')
    expect(a.body).toContain('+new line')
    expect(a.body).not.toContain('docs/new.md')
  })

  it('空 diff 就是没有文件，不是一个空文件', () => {
    expect(splitDiffByFile('')).toEqual([])
    expect(splitDiffByFile('\n\n')).toEqual([])
  })

  // 带空格/中文的路径 git 会加引号并转义，读错的话这个文件就永远点不开。
  it('带空格的路径读的是 b 侧，且解掉引号', () => {
    const d = 'diff --git "a/my docs/a b.md" "b/my docs/a b.md"\n@@ -1 +1 @@\n-x\n+y\n'
    expect(splitDiffByFile(d)[0].path).toBe('my docs/a b.md')
  })

  it('decodes Git UTF-8 octal paths without decoding literal backslashes twice', () => {
    const chinese = String.raw`diff --git "a/docs/\346\226\271\346\241\210.md" "b/docs/\346\226\271\346\241\210.md"`
    expect(splitDiffByFile(chinese)[0].path).toBe('docs/方案.md')
    const literal = String.raw`diff --git "a/docs/\\346.md" "b/docs/\\346.md"`
    expect(splitDiffByFile(literal)[0].path).toBe(String.raw`docs/\346.md`)
  })

  it('读不懂的文件头不会把后面的 diff 一起吞掉', () => {
    const d = `diff --git 乱码
@@ -1 +1 @@
+x
diff --git a/ok.txt b/ok.txt
@@ -1 +1 @@
+y
`
    expect(splitDiffByFile(d).map((f) => f.path)).toEqual(['乱码', 'ok.txt'])
  })
})

describe('diff 行的定性', () => {
  it('增、删、hunk 头、文件头各归各', () => {
    const kinds = parseDiffLines(splitDiffByFile(DIFF)[0].body).map((l) => l.kind)
    expect(kinds).toEqual(['meta', 'meta', 'meta', 'meta', 'hunk', 'context', 'del', 'add', 'add'])
  })

  // `--- a/x` 和一行被删掉的内容都以 - 开头；分不清就会把文件头染成删除行。
  it('文件头的 --- / +++ 不算增删', () => {
    const lines = parseDiffLines('--- a/x\n+++ b/x\n-真的删了\n+真的加了')
    expect(lines.map((l) => l.kind)).toEqual(['meta', 'meta', 'del', 'add'])
  })
})

describe('diff 行号', () => {
  // 行号不是从行本身读出来的，是从 hunk 头起算一步步走下来的：走错一步，后面
  // 每一个号码都跟着错，而画面上照样有数字，看不出错。
  it('从 hunk 头起算，删除只走旧号、新增只走新号', () => {
    const rows = numberDiffLines(parseDiffLines(splitDiffByFile(DIFF)[0].body))
    expect(rows.slice(4).map((r) => [r.kind, r.oldNumber, r.newNumber])).toEqual([
      ['hunk', null, null],
      ['context', 1, 1],
      ['del', 2, null],
      ['add', null, 2],
      ['add', null, 3],
    ])
  })

  it('文件头（diff/index/---/+++）没有行号', () => {
    const rows = numberDiffLines(parseDiffLines(splitDiffByFile(DIFF)[0].body))
    expect(rows.slice(0, 4).map((r) => [r.oldNumber, r.newNumber])).toEqual([
      [null, null],
      [null, null],
      [null, null],
      [null, null],
    ])
  })

  // 新文件的旧号是 0，第一个 + 行是新号 1。这一条正好检验旧号到底从 hunk 头走、
  // 不是从 1 硬起。
  it('新增的第一个文件从新的第 1 行开始', () => {
    const body = splitDiffByFile(DIFF)[1].body
    const adds = numberDiffLines(parseDiffLines(body)).filter((r) => r.kind === 'add')
    expect(adds.map((r) => r.newNumber)).toEqual([1, 2])
    expect(adds.every((r) => r.oldNumber === null)).toBe(true)
  })

  // 真实 git diff（文件最后一行原来没有换行符）：`\ No newline at end of file`
  // 是给上一行做的注解，本身不是一行。当成 context 时旧号和新号都 +1，它后面每一
  // 个号码都跟着错。
  it('「\\ No newline at end of file」不编号，也不推进计数器', () => {
    const body = [
      'diff --git a/notes.txt b/notes.txt',
      'index 3b18e51..a1b2c3d 100644',
      '--- a/notes.txt',
      '+++ b/notes.txt',
      '@@ -1,2 +1,2 @@',
      ' first',
      '-second',
      '\\ No newline at end of file',
      '+second line',
    ].join('\n')
    expect(numberDiffLines(parseDiffLines(body)).map((r) => [r.kind, r.oldNumber, r.newNumber])).toEqual([
      ['meta', null, null],
      ['meta', null, null],
      ['meta', null, null],
      ['meta', null, null],
      ['hunk', null, null],
      ['context', 1, 1],
      ['del', 2, null],
      ['meta', null, null],
      ['add', null, 2],
    ])
  })

  // 整份 diff 以换行结尾，最后一个文件切出来就多一个空串；它不在任何一个 hunk 的
  // 行数里。当成 context 时它拿到下一个号——三行的新文件多出「0 4」，还带一颗
  // 「在第 4 行写批注」。
  it('hunk 行数之外的空行不是文件里的一行', () => {
    const diff = [
      'diff --git a/signup.md b/signup.md',
      'new file mode 100644',
      'index 0000000..e69de29',
      '--- /dev/null',
      '+++ b/signup.md',
      '@@ -0,0 +1,3 @@',
      '+时间：周四下午三点',
      '+地点：B201',
      '+报名找林老师',
      '',
      '',
    ].join('\n')
    const rows = numberDiffLines(parseDiffLines(splitDiffByFile(diff)[0].body))
    expect(rows.filter((r) => r.newNumber !== null).map((r) => r.newNumber)).toEqual([1, 2, 3])
    expect(rows.some((r) => r.text === '')).toBe(false)
  })

  it('hunk 里本来就空着的一行（git 写成一个空格）照样编号', () => {
    const body = ['@@ -1,3 +1,3 @@', ' a', ' ', '-b', '+c', ''].join('\n')
    expect(numberDiffLines(parseDiffLines(body)).map((r) => [r.kind, r.oldNumber, r.newNumber])).toEqual([
      ['hunk', null, null],
      ['context', 1, 1],
      ['context', 2, 2],
      ['del', 3, null],
      ['add', null, 3],
    ])
  })

  // 二进制文件和纯 mode 改动没有内容行：它们该留空，不是显示成「0 0」。
  it('Binary files / old mode 这类行留空', () => {
    const body = [
      'diff --git a/x.png b/x.png',
      'index 1111111..2222222 100644',
      'Binary files a/x.png and b/x.png differ',
    ].join('\n')
    expect(numberDiffLines(parseDiffLines(body)).map((r) => [r.oldNumber, r.newNumber])).toEqual([
      [null, null],
      [null, null],
      [null, null],
    ])
  })
})

describe('大文件的窗口', () => {
  it('窗口是个正数，一次画不完的那一份才有「显示剩余」', () => {
    expect(DIFF_WINDOW).toBeGreaterThan(0)
  })
})

describe('hunkLabel', () => {
  it('names the lines the hunk covers in the reviewed file, and the code it sits in', () => {
    expect(hunkLabel('@@ -205,12 +205,14 @@ class NoticeRepository')).toBe('205–218 · class NoticeRepository')
  })

  it('names a one-line hunk by its line', () => {
    expect(hunkLabel('@@ -3 +3 @@')).toBe('3')
  })

  it('falls back to the old side when the hunk only deletes', () => {
    expect(hunkLabel('@@ -10,3 +9,0 @@')).toBe('10–12')
  })

  it('leaves a header it cannot read as it was', () => {
    expect(hunkLabel('@@ nonsense')).toBe('@@ nonsense')
  })
})
