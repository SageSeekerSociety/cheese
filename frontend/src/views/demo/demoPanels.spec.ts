// 剧本里那几行 diff 变成后端会回的那份 git 输出。文件的 +N −M、树上那一行的增删、
// 点开看到的那一段，三处读的是同一次算术，所以这里盯的就是这个算术：hunk 头里的
// 行数、`---`/`+++`、新增文件那条 `new file mode`。
import { describe, expect, it } from 'vitest'

import { diffOf, previewContentOf, previewInfoOf, progressOf } from './demoPanels'

describe('diffOf', () => {
  it('writes the header and hunk of an added file from the scene’s lines', () => {
    const diff = diffOf({
      files: [{ path: 'README.md', status: 'added', diff: ['+# 课程资料', '+', '+有不清楚的地方，在话题里问。'] }],
    })
    expect(diff).toBe(
      [
        'diff --git a/README.md b/README.md',
        'new file mode 100644',
        'index 0000000..1111111 100644',
        '--- /dev/null',
        '+++ b/README.md',
        '@@ -0,0 +1,3 @@',
        '+# 课程资料',
        '+',
        '+有不清楚的地方，在话题里问。',
      ].join('\n')
    )
  })

  it('counts context, additions and removals of a modified file', () => {
    const diff = diffOf({
      files: [
        {
          path: 'src/a.ts',
          diff: [' const a = 1', '-const b = 2', '+const b = 3', ' const c = 4', '-// 没用的一行'],
        },
      ],
    })
    // 改之前 4 行（2 行上下文 + 2 行删掉），之后 3 行（2 行上下文 + 1 行加上）。
    expect(diff).toContain('@@ -1,4 +1,3 @@')
    expect(diff).toContain('--- a/src/a.ts')
    expect(diff).toContain('+++ b/src/a.ts')
    expect(diff).not.toContain('new file mode')
  })

  it('marks a deleted file as /dev/null on the other side', () => {
    const diff = diffOf({ files: [{ path: 'old.md', status: 'removed', diff: ['-# 旧的一页'] }] })
    expect(diff).toContain('deleted file mode 100644')
    expect(diff).toContain('--- a/old.md')
    expect(diff).toContain('+++ /dev/null')
    expect(diff).toContain('@@ -1,1 +0,0 @@')
  })
})

describe('the other payload builders', () => {
  it('turns the scene’s progress list into the shape the progress bar reads', () => {
    const progress = progressOf({
      progress: [
        { subject: '读题目', status: 'completed' },
        { subject: '写正文', status: 'in_progress' },
      ],
    })
    expect(progress.items.map((i) => i.subject)).toEqual(['读题目', '写正文'])
    expect(progress.updated_at).not.toBeNull()
    expect(progressOf(null).items).toEqual([])
    expect(progressOf(null).updated_at).toBeNull()
  })

  it('answers the preview with the file the scene put on show', () => {
    const preview = { path: '销售分析报告.md', content: '# 销售分析报告' }
    expect(previewInfoOf(preview)?.path).toBe('销售分析报告.md')
    expect(previewInfoOf(null)).toBeNull()
    expect(previewContentOf(preview).content).toBe('# 销售分析报告')
  })
})
