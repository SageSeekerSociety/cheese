import { createVuetify } from 'vuetify'
import { fireEvent, render } from '@testing-library/vue'
import { describe, expect, it } from 'vitest'

import TaskAttachmentList from '../TaskAttachmentList.vue'

import i18n from '@/i18n'

const file = () => ({ name: '讲义.pdf', size: 2048, addedAt: 0 })

const mount = (canDownload: boolean, previewable = false) =>
  render(TaskAttachmentList, {
    props: { attachments: [file()], canDownload, previewable },
    global: { plugins: [createVuetify(), i18n] },
  })

describe('题目材料清单', () => {
  it('拿得到文件的人点下载，报出的是那一个文件', async () => {
    const view = mount(true)

    expect(view.container.textContent).toContain('讲义.pdf')
    await fireEvent.click(view.getByRole('button'))
    expect(view.emitted('download')).toEqual([[expect.objectContaining({ name: '讲义.pdf' })]])
  })

  it('拿不到文件的人看得到清单，但没有下载按钮', () => {
    const view = mount(false)

    expect(view.container.textContent).toContain('讲义.pdf')
    expect(view.queryByRole('button')).toBeNull()
  })

  it('点材料的名字是打开它，不是下载', async () => {
    const view = mount(true, true)

    await fireEvent.click(view.getByRole('button', { name: '讲义.pdf' }))
    expect(view.emitted('open')).toEqual([[expect.objectContaining({ name: '讲义.pdf' })]])
    expect(view.emitted('download')).toBeUndefined()
  })

  it('拿不到文件的人点名字打不开', () => {
    const view = mount(false, true)

    expect(view.queryByRole('button', { name: '讲义.pdf' })).toBeNull()
  })
})
