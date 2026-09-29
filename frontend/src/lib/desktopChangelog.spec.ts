// The changelog is written by hand in desktop/CHANGELOG.md; the download page
// shows what it says, day by day, and nothing else in the file.
import { describe, expect, it } from 'vitest'

import { parseChangelog } from './desktopChangelog'

describe('parseChangelog', () => {
  it('reads each day and its changes, newest first as written', () => {
    const days = parseChangelog(
      [
        '# 更新日志',
        '',
        '写给用的人看的说明。',
        '',
        '## 2026-09-29',
        '',
        '- 关闭窗口后在后台运行',
        '- 程序坞图标显示待办数量',
        '',
        '## 2026-09-25',
        '- 自动更新',
      ].join('\n')
    )
    expect(days).toEqual([
      { date: '2026-09-29', changes: ['关闭窗口后在后台运行', '程序坞图标显示待办数量'] },
      { date: '2026-09-25', changes: ['自动更新'] },
    ])
  })

  it('shows no day that lists no change', () => {
    expect(parseChangelog('## 2026-09-30\n\n## 2026-09-29\n- 新的启动画面')).toEqual([
      { date: '2026-09-29', changes: ['新的启动画面'] },
    ])
  })

  it('has nothing to show for a page that is not the changelog', () => {
    expect(parseChangelog('<!doctype html><html><body>- not a change</body></html>')).toEqual([])
  })
})
