/** 「改动」页签在一个没接代码仓库的项目里：本来就没有东西，不是出了错。
 *
 * 后端对这种项目答的是「项目没有代码仓库」，挂成红色报错会让人以为坏了。
 */
import type { Component } from 'vue'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render, screen } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const getForgeConnection = vi.fn()
const getGitLog = vi.fn()
const getGitDiff = vi.fn()
const listFiles = vi.fn()
const listRoomTasks = vi.fn()
vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    getForgeConnection: (...a: unknown[]) => getForgeConnection(...a),
    getGitLog: (...a: unknown[]) => getGitLog(...a),
    getGitDiff: (...a: unknown[]) => getGitDiff(...a),
    listFiles: (...a: unknown[]) => listFiles(...a),
    listRoomTasks: (...a: unknown[]) => listRoomTasks(...a),
  }
})

import PanelChanges from './PanelChanges.vue'

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

beforeEach(() => {
  listRoomTasks.mockReset().mockResolvedValue({ data: [], total: 0 })
  const noRepo = () => Promise.reject(new Error('项目没有代码仓库'))
  getGitLog.mockReset().mockImplementation(noRepo)
  getGitDiff.mockReset().mockImplementation(noRepo)
  listFiles.mockReset().mockImplementation(noRepo)
  getForgeConnection.mockReset()
})

function mount() {
  return render(PanelChanges as unknown as Component, {
    props: { topicId: 'room-1', projectId: 'p1', active: true },
    global: { plugins: [vuetify] },
  })
}

describe('没接代码仓库的项目', () => {
  it('说「暂无代码仓库」，不挂报错', async () => {
    getForgeConnection.mockResolvedValue({ kind: 'forgejo', connected: false, repo: null, url: null })
    mount()
    expect(await screen.findByText('暂无代码仓库')).toBeTruthy()
    expect(screen.queryByText('项目没有代码仓库')).toBeNull()
  })

  it('接了仓库却拿不到文件时，那句错照样让人看见', async () => {
    getForgeConnection.mockResolvedValue({ kind: 'forgejo', connected: true, repo: 'a/b', url: 'x' })
    mount()
    expect(await screen.findByText('项目没有代码仓库')).toBeTruthy()
    expect(screen.queryByText('暂无代码仓库')).toBeNull()
  })
})
