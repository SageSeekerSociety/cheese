/**
 * 一项产物自己那一页。
 *
 * 它回答两个问题：现在是第几版，以及每一版交出去的那一份还拿不拿得到。三种交法在
 * 这一页上必须是三句不同的话 —— 一份文件能下载、一个地址能打开、一次合并没有东
 * 西可拿，而「这一版没有留存文件」是第四种情况，不能和最后那一种混在一起说。
 */
import type { ArtifactVersion } from '../api'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import ProjectArtifactView from './ProjectArtifactView.vue'

vi.mock('../api', () => ({
  getProjectArtifact: vi.fn(),
  downloadFile: vi.fn(),
  artifactVersionFileUrl: (projectId: string, artifactId: string, cardId: string) =>
    `/api/projects/${projectId}/artifacts/${artifactId}/versions/${cardId}/file`,
}))

const { downloadFile, getProjectArtifact } = await import('../api')

const vuetify = createVuetify({ components, directives })

afterEach(cleanup)

const FILE_VERSION = {
  number: 1,
  card_id: 'c1',
  subject: 'docs(report): first draft',
  delivered_at: '2026-09-18T10:00:00Z',
  decided_by: '林知行',
  kind: 'file' as const,
  filename: '结题报告.pdf',
  url: null,
}

const LINK_VERSION = {
  number: 2,
  card_id: 'c2',
  subject: 'feat(site): publish',
  delivered_at: '2026-09-19T10:00:00Z',
  decided_by: '林知行',
  kind: 'link' as const,
  filename: null,
  url: 'https://example.com/site',
}

function detail(versions: ArtifactVersion[]) {
  return {
    id: 'a1',
    name: '结题报告',
    version: versions.length,
    delivered_at: '2026-09-19T10:00:00Z',
    versions,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(getProjectArtifact).mockResolvedValue(detail([FILE_VERSION, LINK_VERSION]))
  vi.mocked(downloadFile).mockResolvedValue(undefined)
})

function mount() {
  return render(ProjectArtifactView, {
    props: { projectId: 'p1', artifactId: 'a1' },
    global: { plugins: [vuetify] },
  })
}

describe('一项产物', () => {
  it('名字、当前是第几版，以及每一版各自一行', async () => {
    const { container } = mount()

    await waitFor(() => expect(container.textContent).toContain('结题报告'))
    const text = container.textContent ?? ''
    expect(text).toContain('第 2 版')
    expect(text).toContain('版本历史')
    expect(text).toContain('第 1 版')
    expect(text).toContain('docs(report): first draft')
    expect(text).toContain('林知行 验收')
  })

  it('下载取的是这一版当时那一份，不是重新构建的结果', async () => {
    const { container } = mount()
    await waitFor(() => expect(container.textContent).toContain('结题报告'))

    const download = Array.from(container.querySelectorAll('button')).find((b) => b.textContent?.trim() === '下载')
    await fireEvent.click(download!)

    await waitFor(() =>
      expect(downloadFile).toHaveBeenCalledWith('/api/projects/p1/artifacts/a1/versions/c1/file', '结题报告.pdf')
    )
  })

  it('交出去的是地址时给的是打开，不是下载', async () => {
    const { container } = mount()
    await waitFor(() => expect(container.textContent).toContain('结题报告'))

    const opens = Array.from(container.querySelectorAll('a')).filter((a) => a.textContent?.trim() === '打开')
    expect(opens.length).toBeGreaterThan(0)
    expect(opens[0].getAttribute('href')).toBe('https://example.com/site')
  })

  it('交出去的是一次合并时说清没有东西可拿', async () => {
    vi.mocked(getProjectArtifact).mockResolvedValue(
      detail([{ ...FILE_VERSION, kind: 'merge' as const, filename: null }])
    )

    const { container } = mount()

    await waitFor(() => expect(container.textContent).toContain('第 1 版'))
    expect(container.textContent).toContain('交出去的是这次合并')
  })

  it('交付物留存之前的那几版说的是另一句话', async () => {
    vi.mocked(getProjectArtifact).mockResolvedValue(detail([{ ...FILE_VERSION, kind: null, filename: null }]))

    const { container } = mount()

    await waitFor(() => expect(container.textContent).toContain('第 1 版'))
    expect(container.textContent).toContain('这一版没有留存文件')
    expect(container.textContent).not.toContain('交出去的是这次合并')
  })

  it('还没交付过的一项说尚未交付，而不是第 0 版', async () => {
    vi.mocked(getProjectArtifact).mockResolvedValue({ ...detail([]), version: 0, delivered_at: null })

    const { container } = mount()

    await waitFor(() => expect(container.textContent).toContain('尚未交付'))
    expect(container.textContent).toContain('暂无交付')
    expect(container.textContent).not.toContain('第 0 版')
  })
})
