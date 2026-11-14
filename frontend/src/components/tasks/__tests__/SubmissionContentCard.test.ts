/**
 * What the reviewer sees of a file item: every file handed in under it, and a
 * Markdown report drawn with the images it refers to taken from the files
 * handed in beside it.
 */
import type { Material, TaskSubmissionContent } from '@/types'

import { createVuetify } from 'vuetify'
import { cleanup, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import SubmissionContentCard from '../SubmissionContentCard.vue'

import i18n, { setLocale } from '@/i18n'

function file(id: number, name: string, url: string, size = 10): Material {
  return { id, type: 'file', url, meta: { name, size, mime: 'application/octet-stream' } } as Material
}

function show(content: TaskSubmissionContent) {
  return render(SubmissionContentCard, { props: { content }, global: { plugins: [createVuetify(), i18n] } })
}

beforeEach(() => setLocale('zh-CN'))

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('a file item handed in with several files', () => {
  const content: TaskSubmissionContent = {
    title: '实验报告',
    type: 'FILE',
    contentAttachments: [
      file(1, 'report.md', 'https://files.test/r.md'),
      file(2, 'autograder.png', 'https://files.test/a.png'),
      file(3, 'chart.png', 'https://files.test/c.png'),
    ],
  }

  it('lists every file, each opening its own link', () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('# 报告'))
    )
    const view = show(content)
    for (const [name, href] of [
      ['report.md', 'https://files.test/r.md'],
      ['autograder.png', 'https://files.test/a.png'],
      ['chart.png', 'https://files.test/c.png'],
    ]) {
      expect(view.getByText(name).closest('a')?.getAttribute('href')).toBe(href)
    }
  })

  it("draws the report with its screenshots taken from the item's other files", async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) =>
        url === 'https://files.test/r.md'
          ? new Response('# 实验一\n\n![评测结果](autograder.png)\n\n![](./img/chart.png)\n\n![](missing.png)\n')
          : new Response('', { status: 404 })
      )
    )
    const view = show(content)

    await waitFor(() => expect(view.getByText('实验一')).toBeTruthy())
    await waitFor(() => expect(view.container.querySelectorAll('img')).toHaveLength(3))
    const srcs = Array.from(view.container.querySelectorAll('img')).map((img) => img.getAttribute('src'))
    expect(srcs).toEqual(['https://files.test/a.png', 'https://files.test/c.png', null])
  })

  it('says so when the report cannot be read, and still lists its files', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('', { status: 500 }))
    )
    const view = show(content)

    await waitFor(() => expect(view.getByText('无法读取这个文件')).toBeTruthy())
    expect(view.getByText('autograder.png')).toBeTruthy()
  })
})
