/**
 * 一项产物自己那一页。
 *
 * 打开先看到的是当前这一版本身；每一版说清它出自哪次对话、能不能拿走——一份文件能
 * 下载、一个地址能打开、一次合并没有东西可拿，而「这一版没有留存文件」是第四种情况，
 * 不能和最后那一种混在一起说。要看某一版比上一版改了什么，从那一版进去，地址上记着
 * 比的是哪两版。
 */
import type { ArtifactVersion } from '../api'

import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { setLocale } from '../i18n'

import ProjectArtifactView from './ProjectArtifactView.vue'

vi.mock('../api', () => ({
  getProjectArtifact: vi.fn(),
  downloadFile: vi.fn(),
  compareArtifactVersions: vi.fn(),
  artifactVersionBytes: vi.fn(),
  artifactVersionFileUrl: (projectId: string, artifactId: string, cardId: string) =>
    `/api/projects/${projectId}/artifacts/${artifactId}/versions/${cardId}/file`,
}))

const { downloadFile, getProjectArtifact, compareArtifactVersions, artifactVersionBytes } = await import('../api')

afterEach(cleanup)

const FILE_VERSION: ArtifactVersion = {
  number: 1,
  card_id: 'c1',
  subject: 'docs(report): first draft',
  delivered_at: '2026-09-18T10:00:00Z',
  decided_by: '林知行',
  kind: 'file',
  filename: '结题报告.pdf',
  url: null,
  bytes: 2048,
  room: { id: 't1', title: '结题报告修订' },
}

const LINK_VERSION: ArtifactVersion = {
  number: 2,
  card_id: 'c2',
  subject: 'feat(site): publish',
  delivered_at: '2026-09-19T10:00:00Z',
  decided_by: '林知行',
  kind: 'link',
  filename: null,
  url: 'https://example.com/site',
  bytes: null,
  room: null,
}

function detail(versions: ArtifactVersion[]) {
  return {
    id: 'a1',
    name: '结题报告',
    about: '交给甲方的最终报告',
    version: versions.length,
    delivered_at: versions.at(-1)?.delivered_at ?? null,
    versions,
  }
}

function files(n: number): ArtifactVersion[] {
  return Array.from({ length: n }, (_, i) => ({
    ...FILE_VERSION,
    number: i + 1,
    card_id: `c${i + 1}`,
    filename: 'report.txt',
  }))
}

beforeEach(() => {
  setLocale('zh-CN')
  vi.clearAllMocks()
  vi.mocked(getProjectArtifact).mockResolvedValue(detail([FILE_VERSION, LINK_VERSION]))
  vi.mocked(downloadFile).mockResolvedValue(undefined)
  vi.mocked(compareArtifactVersions).mockResolvedValue({
    kind: 'unavailable',
    identical: null,
    note: 'unavailable',
    files: [],
  })
  vi.mocked(artifactVersionBytes).mockResolvedValue(new ArrayBuffer(0))
})

const Blank = defineComponent({ render: () => h('div') })

async function mount(url = '/projects/p1/artifacts/a1') {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      {
        path: '/projects/:projectId/artifacts/:artifactId',
        name: 'project-artifact',
        component: ProjectArtifactView,
        props: true,
      },
      { path: '/projects/:projectId/topics/:topicId', name: 'workspace-topic', component: Blank },
      { path: '/projects/:projectId/running', name: 'workspace-running', component: Blank },
    ],
  })
  await router.push(url)
  await router.isReady()
  const Host = defineComponent({ setup: () => () => h(components.VApp, null, () => h(RouterView)) })
  const view = render(Host, {
    global: { plugins: [createVuetify({ components, directives }), router, createPinia()] },
  })
  return { ...view, router }
}

describe('一项产物', () => {
  it('打开就是当前这一版本身：读它当时交出去的那一份，不去比较', async () => {
    vi.mocked(getProjectArtifact).mockResolvedValue(detail(files(3)))
    await mount()
    await waitFor(() => expect(artifactVersionBytes).toHaveBeenCalledWith('p1', 'a1', 'c3', false))
    expect(artifactVersionBytes).not.toHaveBeenCalledWith('p1', 'a1', 'c2', expect.anything())
    expect(compareArtifactVersions).not.toHaveBeenCalled()
  })

  it('名字、当前是第几版，以及每一版各自一行', async () => {
    const { container } = await mount()
    await waitFor(() => expect(container.textContent).toContain('版本历史'))
    const text = container.textContent ?? ''
    expect(text).toContain('结题报告')
    expect(text).toContain('第 2 版')
    expect(text).toContain('第 1 版')
    expect(text).toContain('docs(report): first draft')
    expect(text).toContain('林知行 采纳')
  })

  it('每一版写明出自哪次对话，点过去就是那个房间；读不了的房间不写名字', async () => {
    const { container } = await mount()
    await waitFor(() => expect(container.textContent).toContain('《结题报告修订》'))
    const link = screen.getByRole('link', { name: '《结题报告修订》' })
    expect(link.getAttribute('href')).toBe('/projects/p1/topics/t1')
    expect(container.querySelectorAll('a[href^="/projects/p1/topics/"]')).toHaveLength(1)
  })

  it('下载取的是这一版当时那一份，不是重新构建的结果', async () => {
    const { container } = await mount()
    await waitFor(() => expect(container.textContent).toContain('版本历史'))
    await fireEvent.click(screen.getByRole('button', { name: '下载' }))
    await waitFor(() =>
      expect(downloadFile).toHaveBeenCalledWith('/api/projects/p1/artifacts/a1/versions/c1/file', '结题报告.pdf')
    )
  })

  it('交出去的是地址时给的是打开，不是下载', async () => {
    const { container } = await mount()
    await waitFor(() => expect(container.textContent).toContain('版本历史'))
    const opens = screen.getAllByRole('link').filter((a) => a.textContent?.trim() === '打开')
    expect(opens.length).toBeGreaterThan(0)
    expect(opens[0].getAttribute('href')).toBe('https://example.com/site')
  })

  it('交出去的是一次合并时，当前这一版给出它比上一版改了的文件', async () => {
    const merge = (n: number): ArtifactVersion => ({
      ...FILE_VERSION,
      number: n,
      card_id: `c${n}`,
      kind: 'merge',
      filename: null,
    })
    vi.mocked(getProjectArtifact).mockResolvedValue(detail([merge(1), merge(2)]))
    vi.mocked(compareArtifactVersions).mockResolvedValue({
      kind: 'merge',
      identical: false,
      note: 'source',
      files: [{ path: 'app/main.py', status: 'modified', diff: '@@ -1 +1 @@\n-old\n+new', note: null }],
    })
    const { container } = await mount()
    await waitFor(() => expect(container.textContent).toContain('app/main.py'))
    expect(compareArtifactVersions).toHaveBeenCalledWith('p1', 'a1', 'c1', 'c2')
    expect(container.textContent).toContain('交出去的是这次合并')
  })

  it('交付物留存之前的那几版说的是另一句话', async () => {
    vi.mocked(getProjectArtifact).mockResolvedValue(detail([{ ...FILE_VERSION, kind: null, filename: null }]))
    const { container } = await mount()
    await waitFor(() => expect(container.textContent).toContain('第 1 版'))
    expect(container.textContent).toContain('这一版没有留存文件')
    expect(container.textContent).not.toContain('交出去的是这次合并')
  })

  it('还没交付过的一项说尚未交付，而不是第 0 版', async () => {
    vi.mocked(getProjectArtifact).mockResolvedValue({ ...detail([]), version: 0, delivered_at: null })
    const { container } = await mount()
    await waitFor(() => expect(container.textContent).toContain('尚未交付'))
    expect(container.textContent).toContain('暂无交付')
    expect(container.textContent).not.toContain('第 0 版')
  })

  it('从某一版进去比它和上一版，地址记着是哪两版', async () => {
    vi.mocked(getProjectArtifact).mockResolvedValue(detail(files(3)))
    vi.mocked(compareArtifactVersions).mockResolvedValue({
      kind: 'file',
      identical: false,
      note: null,
      files: [{ path: 'report.txt', diff: '@@ -1 +1 @@\n-旧正文\n+新正文', note: null }],
    })
    const { router, container } = await mount()
    await waitFor(() => expect(container.textContent).toContain('版本历史'))
    await fireEvent.click(screen.getByRole('button', { name: '与第 2 版比较' }))
    await waitFor(() => expect(router.currentRoute.value.query).toMatchObject({ before: 'c2', after: 'c3' }))
    await waitFor(() => expect(compareArtifactVersions).toHaveBeenCalledWith('p1', 'a1', 'c2', 'c3'))
  })

  it('换了比较对象，还在路上的旧结果回来也不显示', async () => {
    vi.mocked(getProjectArtifact).mockResolvedValue(detail(files(3)))
    let finish!: (value: Awaited<ReturnType<typeof compareArtifactVersions>>) => void
    vi.mocked(compareArtifactVersions).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve
        })
    )
    vi.mocked(compareArtifactVersions).mockResolvedValueOnce({ kind: 'file', identical: true, note: null, files: [] })
    const { container } = await mount('/projects/p1/artifacts/a1?before=c2&after=c3')
    await waitFor(() => expect(compareArtifactVersions).toHaveBeenCalledWith('p1', 'a1', 'c2', 'c3'))
    await fireEvent.update(screen.getByLabelText('比较对象'), 'c1')
    await waitFor(() => expect(compareArtifactVersions).toHaveBeenCalledWith('p1', 'a1', 'c1', 'c3'))
    finish({
      kind: 'file',
      identical: false,
      note: null,
      files: [{ path: 'report.txt', diff: '@@ -1 +1 @@\n-甲\n+过期响应', note: null }],
    })
    await waitFor(() => expect(container.textContent).toContain('两个版本的内容相同'))
    expect(container.textContent).not.toContain('过期响应')
  })

  it('比不出逐字差异的两版说清原因，并把两版当时那一份并排摆出来', async () => {
    vi.mocked(getProjectArtifact).mockResolvedValue(
      detail([FILE_VERSION, { ...FILE_VERSION, number: 2, card_id: 'c2' }])
    )
    vi.mocked(compareArtifactVersions).mockResolvedValue({
      kind: 'file',
      identical: false,
      note: null,
      files: [{ path: '结题报告.pdf', diff: null, note: 'binary' }],
    })
    const { container } = await mount('/projects/p1/artifacts/a1?before=c1&after=c2')
    await waitFor(() => expect(container.textContent).toContain('此格式不提供逐行差异'))
    await waitFor(() => expect(artifactVersionBytes).toHaveBeenCalledWith('p1', 'a1', 'c1', false))
    expect(artifactVersionBytes).toHaveBeenCalledWith('p1', 'a1', 'c2', false)
  })
})
