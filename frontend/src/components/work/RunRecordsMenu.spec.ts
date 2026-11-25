/**
 * 运行记录 lists what a person can run again in their own terminal: the project's named commands
 * first, then the commands the AI teammate ran in the task, newest first, each opening onto what
 * it printed. 「再运行」 hands the command on; 「固定」 keeps it in the project under a name, and the
 * named list shows it at once. A list read for one task never shows up on another.
 *
 * Mounted the way the page mounts it: the menu fed by `useRunRecords`, over a stand-in API that
 * keeps the project's named commands as the server would.
 */
import type { Component } from 'vue'
import type { NamedCommand, RanCommand, RanCommandsPage } from '@/api'

import { defineComponent, h, ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import { setLocale } from '@/i18n'

function ran(call: string, command: string, extra: Partial<RanCommand> = {}): RanCommand {
  return {
    call_id: call,
    command,
    description: '',
    background: false,
    output: null,
    failed: false,
    at: '2026-11-24T05:00:00+00:00',
    turn_id: null,
    agent_handle: 'cheese',
    ...extra,
  }
}

const server = {
  pages: {} as Record<string, RanCommandsPage>,
  named: [] as NamedCommand[],
  /** A read held back until the test lets it go. */
  hold: null as null | { task: string; release: () => void },
}

vi.mock('@/api', () => ({
  listRanCommands: async (task: string) => {
    if (server.hold?.task === task) await new Promise<void>((go) => (server.hold!.release = go))
    return server.pages[task] ?? { items: [], next: null }
  },
  listNamedCommands: async () => [...server.named],
  nameCommand: async (_project: string, name: string, command: string) => {
    const row = { id: `n-${name}`, name, command, fixed_by: 'user-1', fixed_at: '' }
    server.named = [...server.named.filter((one) => one.name !== name), row]
    return row
  },
  removeNamedCommand: async (_project: string, id: string) => {
    server.named = server.named.filter((one) => one.id !== id)
  },
}))

import { useRunRecords } from '../../composables/useRunRecords'

import RunRecordsMenu from './RunRecordsMenu.vue'

let vuetify: ReturnType<typeof createVuetify>
beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})
beforeEach(() => {
  setLocale('zh-CN')
  // The menu places itself against the visual viewport, which a DOM test does not have.
  vi.stubGlobal('visualViewport', {
    width: 1280,
    height: 800,
    offsetLeft: 0,
    offsetTop: 0,
    scale: 1,
    addEventListener() {},
    removeEventListener() {},
  })
  server.pages = {
    'task-1': {
      items: [
        ran('c4', 'make grade', { output: 'test_fork: FAIL', failed: true }),
        ran('c1', 'make', { output: 'ok', description: 'Build the kernel' }),
      ],
      next: null,
    },
    'task-2': { items: [ran('d1', 'psql -f schema.sql', { output: 'CREATE TABLE' })], next: null },
  }
  server.named = [{ id: 'n-测试', name: '测试', command: 'make test', fixed_by: 'user-1', fixed_at: '' }]
  server.hold = null
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function mount() {
  const task = ref('task-1')
  const runs: string[] = []
  const Shell = defineComponent({
    setup() {
      const records = useRunRecords(
        () => task.value,
        () => 'project-1'
      )
      return () =>
        h(RunRecordsMenu as unknown as Component, {
          named: records.named.value,
          ran: records.ran.value,
          more: !!records.next.value,
          loading: records.loading.value,
          failed: records.failed.value,
          nameFailed: records.nameFailed.value,
          canName: true,
          agentName: '芝士',
          onOpen: () => records.load(),
          onRun: (command: string) => runs.push(command),
          onName: async (label: string, command: string, done: (kept: boolean) => void) =>
            done(await records.name(label, command)),
          onRemove: (id: string) => records.remove(id),
        })
    },
  })
  render(Shell, { global: { plugins: [vuetify] } })
  return { task, runs }
}

async function openList() {
  await fireEvent.click(screen.getByRole('button', { name: '运行记录' }))
  return screen.findByRole('dialog', { name: '运行记录' })
}

describe('运行记录', () => {
  it('lists the named commands first, then what the teammate ran, newest first', async () => {
    mount()
    const list = await openList()
    await waitFor(() => expect(list.textContent).toContain('make grade'))
    const text = list.textContent ?? ''
    expect(text.indexOf('make test')).toBeLessThan(text.indexOf('make grade'))
    expect(text.indexOf('make grade')).toBeLessThan(text.indexOf('Build the kernel'))
    expect(screen.getByText('芝士运行过的命令')).toBeTruthy()
    expect(screen.getByText('失败')).toBeTruthy()
  })

  it('opens a command onto what it printed', async () => {
    mount()
    await openList()
    await fireEvent.click(await screen.findByRole('button', { name: /make grade/ }))
    expect(screen.getByText('test_fork: FAIL')).toBeTruthy()
  })

  it('再运行 hands the command on, named or ran', async () => {
    const { runs } = mount()
    await openList()
    await screen.findByText('make grade')
    const buttons = screen.getAllByRole('button', { name: '再运行' })
    await fireEvent.click(buttons[0])
    await openList()
    await fireEvent.click((await screen.findAllByRole('button', { name: '再运行' }))[1])
    expect(runs).toEqual(['make test', 'make grade'])
  })

  it('固定 keeps a command under a name, and the named list shows it at once', async () => {
    mount()
    await openList()
    await screen.findByText('make grade')
    await fireEvent.click(screen.getAllByRole('button', { name: '固定' })[0])
    await fireEvent.update(screen.getByRole('textbox', { name: '名称' }), '评分')
    await fireEvent.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() => expect(server.named.map((one) => one.name)).toContain('评分'))
    expect(await screen.findByText('评分')).toBeTruthy()
    expect(screen.queryByRole('textbox', { name: '名称' })).toBeNull()
  })

  it('移除 takes a named command off', async () => {
    mount()
    await openList()
    await fireEvent.click(await screen.findByRole('button', { name: '移除「测试」' }))
    await waitFor(() => expect(screen.queryByText('make test')).toBeNull())
    expect(server.named).toEqual([])
  })

  it('a list read for one task that answers after the page moved on never shows on the next', async () => {
    const { task } = mount()
    server.hold = { task: 'task-1', release: () => {} }
    await openList()
    task.value = 'task-2'
    await fireEvent.click(screen.getByRole('button', { name: '运行记录' }))
    await fireEvent.click(screen.getByRole('button', { name: '运行记录' }))
    await waitFor(() => expect(screen.getByText('psql -f schema.sql')).toBeTruthy())
    server.hold.release()
    await new Promise((go) => setTimeout(go, 20))
    expect(screen.queryByText('make grade')).toBeNull()
  })
})
