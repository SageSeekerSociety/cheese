/** 一枚 chip 指名要看一份文件，「改动」这一格回话：开成了没有。
 *
 * 工作面板拿这句回话挑格子：开成了就停在「改动」上；没开成就退到只读的「项目当前
 * 版本」那一格去开它。所以「没开成」不只是「清单里没有这份文件」——清单根本没拿到
 * （这件任务找不到、项目没接仓库、取清单出错）也是没开成。答错成「开成了」，读者
 * 点了一份文件，落到一格开不出它的「改动」上。
 */
import type { Component } from 'vue'

import { defineComponent, h, ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render, screen } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import { setLocale } from '@/i18n'

beforeEach(() => setLocale('zh-CN'))

const getForgeConnection = vi.fn()
const getGitDiff = vi.fn()
const listFiles = vi.fn()
const listRoomTasks = vi.fn()
const readFile = vi.fn()
vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    getForgeConnection: (...a: unknown[]) => getForgeConnection(...a),
    getGitDiff: (...a: unknown[]) => getGitDiff(...a),
    listFiles: (...a: unknown[]) => listFiles(...a),
    listRoomTasks: (...a: unknown[]) => listRoomTasks(...a),
    readFile: (...a: unknown[]) => readFile(...a),
  }
})
vi.mock('../CodeEditor.vue', () => ({
  default: { name: 'CodeEditorStub', props: ['modelValue', 'filename', 'readonly'], template: '<div />' },
}))

import PanelChangesHost from '../work/PanelChangesHost.vue'

let vuetify: ReturnType<typeof createVuetify>

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
})

const TASK = {
  id: 't-1',
  room_id: 'room-1',
  status: 'open',
  branch_name: 'cheese/t-1',
  presentation: { column: 'building', phrase: 'running' },
}

beforeEach(() => {
  getForgeConnection.mockReset().mockResolvedValue({ kind: 'forgejo', connected: true, repo: 'o/r', url: null })
  getGitDiff.mockReset().mockResolvedValue({ diff: '' })
  listRoomTasks.mockReset().mockResolvedValue({ data: [TASK], total: 1 })
  listFiles.mockReset().mockResolvedValue({ data: [{ path: 'src/a.ts', bytes: 10 }], total: 1, source: 'live' })
  readFile.mockReset().mockResolvedValue({
    path: 'src/a.ts',
    content: 'const a = 1\n',
    version: 'v1',
    bytes: 12,
    binary: false,
    too_large: false,
  })
})

async function flush() {
  for (let i = 0; i < 8; i += 1) await new Promise((r) => setTimeout(r, 0))
}

/** 「改动」那一格，和工作面板手上那根拿来开文件的线。 */
async function mount() {
  const panel = ref<{ openFile: (path: string) => Promise<boolean> } | null>(null)
  const Host = defineComponent({
    setup: () => () =>
      h(PanelChangesHost as unknown as Component, {
        ref: panel,
        topicId: 'room-1',
        taskId: 't-1',
        projectId: 'p1',
        active: true,
      }),
  })
  render(Host, { global: { plugins: [vuetify] } })
  await flush()
  return (path: string) => panel.value!.openFile(path)
}

describe('chip 指名的文件，「改动」那一格开没开成', () => {
  it('这一支的工作副本里有它：开成了', async () => {
    const open = await mount()
    expect(await open('src/a.ts')).toBe(true)
  })

  it('这一支的工作副本里没有它：没开成', async () => {
    const open = await mount()
    expect(await open('docs/old.md')).toBe(false)
  })

  it('取清单出错：没开成，好让工作面板换一格去开', async () => {
    listFiles.mockRejectedValue(new Error('boom'))
    const open = await mount()
    expect(await open('src/a.ts')).toBe(false)
  })

  it('这件任务找不到：没开成', async () => {
    listRoomTasks.mockResolvedValue({ data: [], total: 0 })
    const open = await mount()
    expect(await open('src/a.ts')).toBe(false)
  })

  it('项目没接代码仓库：没开成', async () => {
    getForgeConnection.mockResolvedValue({ kind: 'forgejo', connected: false, repo: null, url: null })
    listFiles.mockRejectedValue(new Error('项目没有代码仓库'))
    const open = await mount()
    // 这一格已经认出项目没接仓库，再来开文件时它连清单都不去取。
    expect(await screen.findByText('暂无代码仓库')).toBeTruthy()
    expect(await open('src/a.ts')).toBe(false)
  })
})
