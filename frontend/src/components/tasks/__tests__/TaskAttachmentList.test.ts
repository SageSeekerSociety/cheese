import { createVuetify } from 'vuetify'
import { fireEvent, render } from '@testing-library/vue'
import { describe, expect, it } from 'vitest'

import TaskAttachmentList from '../TaskAttachmentList.vue'

import i18n from '@/i18n'

const file = (overrides: Record<string, unknown> = {}) => ({
  id: 3,
  name: '讲义.pdf',
  size: 2048,
  contentType: 'application/pdf',
  uploaderId: 1,
  downloadCount: 0,
  createdAt: 0,
  ...overrides,
})

const mount = (canDownload: boolean) =>
  render(TaskAttachmentList, {
    props: { attachments: [file()], canDownload },
    global: { plugins: [createVuetify(), i18n] },
  })

describe('题目材料清单', () => {
  it('拿得到文件的人点下载，报出的是那一个文件', async () => {
    const view = mount(true)

    expect(view.container.textContent).toContain('讲义.pdf')
    await fireEvent.click(view.getByRole('button'))
    expect(view.emitted('download')).toEqual([[expect.objectContaining({ id: 3, name: '讲义.pdf' })]])
  })

  it('拿不到文件的人看得到清单，但没有下载按钮', () => {
    const view = mount(false)

    expect(view.container.textContent).toContain('讲义.pdf')
    expect(view.queryByRole('button')).toBeNull()
  })
})
