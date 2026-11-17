// 资料库 › 项目文件的目录树和开着的那一份：点文件夹就地开合、点文件打开它、一搜就只剩对得上
// 的文件；开着的那一份说出它最后一次来自哪件任务，点任务就去那件任务。
//
// 这一层只摆、只报：开哪一份、去哪件任务、新建任务都报给页面。
import type { ProjectChange, WorkspaceFile } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import ProjectFilesBrowser from './ProjectFilesBrowser.vue'

import { setLocale, t } from '@/i18n'

afterEach(cleanup)
beforeEach(() => setLocale('zh-CN'))

const FILES: WorkspaceFile[] = [
  { path: 'README.md', bytes: 100 },
  { path: '报告/月度分析报告.md', bytes: 1800 },
  { path: '分析/统计.py', bytes: 480 },
]

function mount(props: Partial<InstanceType<typeof ProjectFilesBrowser>['$props']> = {}) {
  return render(ProjectFilesBrowser, {
    props: {
      files: FILES,
      query: '',
      collapsedDirs: new Set<string>(),
      viewing: null,
      change: null,
      read: null,
      wide: true,
      ...props,
    },
    global: {
      plugins: [createVuetify({ components, directives })],
      // The bytes preview reads and renders a file; what it draws is its own spec's business.
      stubs: { FileBytesPreview: true },
    },
  })
}

const row = (name: string) => screen.getByText(name).closest('button') as HTMLElement

describe('项目文件的目录树', () => {
  it('点文件夹报的是开合，点文件报的是打开那一份', async () => {
    const { emitted } = mount()

    await fireEvent.click(row('报告'))
    await fireEvent.click(row('统计.py'))

    expect(emitted()['toggle-dir']).toEqual([['报告']])
    expect(emitted().select).toEqual([['分析/统计.py']])
  })

  it('收起的文件夹不摊出里面的文件', () => {
    mount({ collapsedDirs: new Set(['报告']) })

    expect(screen.queryByText('月度分析报告.md')).toBeNull()
    expect(screen.getByText('统计.py')).toBeTruthy()
  })

  it('一搜就只剩名字对得上的文件，写完整路径', () => {
    mount({ query: '报告' })

    expect(screen.getByText('报告/月度分析报告.md')).toBeTruthy()
    expect(screen.queryByText('README.md')).toBeNull()
    expect(screen.queryByText('统计.py')).toBeNull()
  })

  it('什么都没搜到时说暂无匹配', () => {
    mount({ query: '不存在的文件' })

    expect(screen.getByText(t('work.library.projectFiles.noMatch'))).toBeTruthy()
  })

  it('要改文件就报新建任务', async () => {
    const { emitted } = mount()

    await fireEvent.click(screen.getByText(t('work.library.projectFiles.newTask')))

    expect(emitted()['new-task']).toHaveLength(1)
  })
})

describe('开着的那一份', () => {
  const viewing = FILES[1]
  const read = () => Promise.resolve(new ArrayBuffer(0))

  it('来自任务的改动说出那件任务，点它就去那件任务', async () => {
    const change: ProjectChange = {
      sha: 'a1',
      subject: 'feat: 补趋势图',
      author: '王珊',
      at: '2026-11-10T06:00:00Z',
      task: { id: 'task-7', title: '月度报告补趋势图', room_id: 'room-1' },
    }
    const { emitted } = mount({ viewing, read, change })

    await fireEvent.click(screen.getByText(t('work.library.projectFiles.taskTitle', { title: '月度报告补趋势图' })))

    expect(emitted()['open-task']).toEqual([['task-7']])
  })

  it('直接推上来的改动不编一件任务，只写是谁', () => {
    const change: ProjectChange = {
      sha: 'b2',
      subject: 'docs: readme',
      author: '陈默',
      at: '2026-11-10T06:00:00Z',
      task: null,
    }
    mount({ viewing, read, change })

    expect(screen.getByText('陈默')).toBeTruthy()
    expect(screen.queryByText(/「/)).toBeNull()
  })

  it('改动记录和下载都报给页面', async () => {
    const { emitted } = mount({ viewing, read })

    await fireEvent.click(screen.getByText(t('work.library.projectFiles.history')))
    await fireEvent.click(screen.getByText(t('work.library.download')))

    expect(emitted()['file-history']).toHaveLength(1)
    expect(emitted().download).toHaveLength(1)
  })
})
