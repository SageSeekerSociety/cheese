/**
 * 清单上某一版交出去的那一份，画在屏幕上。
 *
 * 这一块只回答一件事：这一版当时交出去的那份快照，现在长什么样。它是「点进去看
 * 看」的落点，所以它自己既不能猜文件类型（猜错就是把一份表格喂给画 PDF 的阅读
 * 器），也不能把「这个部署没有转换服务」和「这份文件转不了」说成同一句话。
 */
import type { ArtifactVersion } from '../api'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { setLocale } from '../i18n'

import ArtifactVersionPreview from './ArtifactVersionPreview.vue'

vi.mock('../api', () => ({
  artifactVersionBytes: vi.fn(),
  downloadFile: vi.fn(),
  PreviewRendererUnavailable: class extends Error {},
  artifactVersionFileUrl: (projectId: string, artifactId: string, cardId: string) =>
    `/api/projects/${projectId}/artifacts/${artifactId}/versions/${cardId}/file`,
}))

// 两个阅读器换掉：这一份 spec 问的是**哪一个**拿到了字节，不是它画得好不好。
vi.mock('./panels/preview/PreviewPages.vue', () => ({
  default: {
    props: ['data'],
    template: '<div data-testid="pages" :data-bytes="data?.byteLength ?? 0" />',
  },
}))
vi.mock('./panels/preview/PreviewSheet.vue', () => ({
  default: {
    props: ['data', 'kind'],
    template: '<div data-testid="sheet" :data-kind="kind" />',
  },
}))

const { artifactVersionBytes, downloadFile, PreviewRendererUnavailable } = await import('../api')

const vuetify = createVuetify({ components, directives })

afterEach(cleanup)

function version(over: Partial<ArtifactVersion> = {}): ArtifactVersion {
  return {
    number: 1,
    card_id: 'c1',
    subject: 'docs(report): finalise',
    delivered_at: '2026-09-19T10:00:00Z',
    decided_by: '林知行',
    kind: 'file',
    filename: '结题报告.docx',
    url: null,
    ...over,
  } as ArtifactVersion
}

function mount(over: Partial<ArtifactVersion> = {}) {
  return render(ArtifactVersionPreview, {
    props: { projectId: 'p1', artifactId: 'a1', version: version(over) },
    global: { plugins: [vuetify] },
  })
}

beforeEach(() => {
  setLocale('zh-CN')
  vi.clearAllMocks()
  vi.mocked(downloadFile).mockResolvedValue(undefined)
  vi.mocked(artifactVersionBytes).mockResolvedValue(new Uint8Array([1, 2, 3, 4]).buffer)
})

describe('一版的预览', () => {
  it('只说要「能给浏览器看的那一份」，后缀该转什么由后端决定', async () => {
    const { getByTestId } = mount()

    // 少传一个「这个后缀要不要转」的参数不是省事：那张表在两边各维护一份，就有
    // 一天不是同一张。旧表格就是这么一路撞进读不懂 BIFF 的阅读器里的。
    await waitFor(() => expect(artifactVersionBytes).toHaveBeenCalledWith('p1', 'a1', 'c1'))
    expect(artifactVersionBytes).toHaveBeenCalledTimes(1)
    await waitFor(() => expect(getByTestId('pages').getAttribute('data-bytes')).toBe('4'))
  })

  it('旧表格拿到的是新表格的字节，交给画表格的阅读器', async () => {
    const { getByTestId, queryByTestId } = mount({ filename: '预算.xls' })

    await waitFor(() => expect(getByTestId('sheet')).toBeTruthy())
    expect(getByTestId('sheet').getAttribute('data-kind')).toBe('workbook')
    // 一份 .xls 一个字节都不该进画 PDF 的那条路。
    expect(queryByTestId('pages')).toBeNull()
  })

  it('CSV 也走表格，但走的是 CSV 那条读法', async () => {
    const { getByTestId } = mount({ filename: '名单.csv' })

    await waitFor(() => expect(getByTestId('sheet').getAttribute('data-kind')).toBe('csv'))
  })

  it('部署没接转换服务时说的是部署的事，而且下载还在', async () => {
    vi.mocked(artifactVersionBytes).mockRejectedValue(new PreviewRendererUnavailable('文档预览服务暂时无法访问'))

    const { container } = mount()

    // 和房间那个文档面板是同一句：这一格说的是部署，不是这一份文件。
    await waitFor(() => expect(container.textContent).toContain('文档预览未启用'))

    const download = Array.from(container.querySelectorAll('button')).find((b) => b.textContent?.trim() === '下载')!
    await fireEvent.click(download)
    // 取的是当时交出去的那一份快照，不是现在从源重建一次的结果。
    await waitFor(() =>
      expect(downloadFile).toHaveBeenCalledWith('/api/projects/p1/artifacts/a1/versions/c1/file', '结题报告.docx')
    )
  })

  it('这份文件转不了时说的是文件的事，不是部署的事', async () => {
    vi.mocked(artifactVersionBytes).mockRejectedValue(new Error('这个格式不能转换为预览：.pages'))

    const { container } = mount()

    await waitFor(() => expect(container.textContent).toContain('这个格式不能转换为预览'))
    // 两句不能说成一句：换一份文件就好了，和换个部署才好，不是同一件事。
    expect(container.textContent).not.toContain('文档预览未启用')
  })

  it('一次合并没有文件可看，这句话和「转不了」也不一样', async () => {
    const { container } = mount({ kind: 'merge', filename: null })

    await waitFor(() => expect(container.textContent).toContain('这次交出去的是合并本身'))
    expect(artifactVersionBytes).not.toHaveBeenCalled()
    expect(container.querySelectorAll('button').length).toBe(0)
  })

  it('交出去的是地址时把地址摆出来', async () => {
    const { container } = mount({ kind: 'link', filename: null, url: 'https://example.com/site' })

    await waitFor(() => expect(container.textContent).toContain('https://example.com/site'))
    expect(container.querySelector('a')?.getAttribute('href')).toBe('https://example.com/site')
  })
})
