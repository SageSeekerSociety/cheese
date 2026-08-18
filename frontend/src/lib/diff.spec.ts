import { describe, expect, it } from 'vitest'

import { parseDiffLines, splitDiffByFile } from './diff'

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
