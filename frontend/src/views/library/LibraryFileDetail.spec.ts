/**
 * 资料库里打开的一份：「评论」说清楚评论在哪，不留一个点了没反应的入口。
 */
import { createVuetify } from 'vuetify'
import { cleanup, fireEvent, render } from '@testing-library/vue'
import { afterEach, expect, it } from 'vitest'

import LibraryFileDetail from './LibraryFileDetail.vue'

import { setLocale } from '@/i18n'

setLocale('zh-CN')
afterEach(cleanup)

function mount(room: { id: string; title: string } | null) {
  return render(LibraryFileDetail, {
    props: {
      shelf: 'proj',
      file: { path: '方案.pdf', bytes: 10, added_at: '2026-10-30T10:00:00Z', added_by: '芝士', room } as never,
      wide: true,
      revision: 0,
      busy: false,
      read: () => new Promise<ArrayBuffer>(() => {}),
    },
    global: {
      plugins: [createVuetify()],
      stubs: { VIcon: true, FileBytesPreview: true, PaneHandle: true, NavLink: { template: '<a><slot /></a>' } },
    },
  })
}

it('从某个任务存进来的：点「评论」指到那个任务', async () => {
  const ui = mount({ id: 't1', title: '招募方案' })
  await fireEvent.click(ui.getByTestId('library-comments'))
  expect(ui.container.textContent).toContain('评论和批注写在它来源的任务里')
  expect(ui.container.textContent).toContain('招募方案')
})

it('不是从任务来的：说清楚要先放进一个任务', async () => {
  const ui = mount(null)
  await fireEvent.click(ui.getByTestId('library-comments'))
  expect(ui.container.textContent).toContain('把它放进一个任务的文件里')
})

it('手机上「评论」在底栏，和下载并排', async () => {
  const ui = render(LibraryFileDetail, {
    props: {
      shelf: 'proj',
      file: { path: '方案.pdf', bytes: 10, added_at: '2026-10-30T10:00:00Z', added_by: '芝士', room: null } as never,
      wide: false,
      revision: 0,
      busy: false,
      read: () => new Promise<ArrayBuffer>(() => {}),
    },
    global: {
      plugins: [createVuetify()],
      stubs: { VIcon: true, FileBytesPreview: true, PaneHandle: true, NavLink: { template: '<a><slot /></a>' } },
    },
  })
  await fireEvent.click(ui.getByTestId('library-comments-phone'))
  expect(ui.container.textContent).toContain('把它放进一个任务的文件里')
})
