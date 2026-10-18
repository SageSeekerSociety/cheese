/** 「添加 MCP 服务器」那张表：远程服务器，或在项目机器上运行的一条命令。只看它交上去的东西。 */
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { afterAll, afterEach, beforeAll, beforeEach, expect, it, vi } from 'vitest'

import i18n, { setLocale } from '../i18n'

import McpServerDialog from './McpServerDialog.vue'

beforeAll(() => {
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
  vi.stubGlobal('visualViewport', {
    width: 1280,
    height: 900,
    offsetLeft: 0,
    offsetTop: 0,
    addEventListener() {},
    removeEventListener() {},
  })
  vi.stubGlobal('devicePixelRatio', 1)
})
afterAll(() => vi.unstubAllGlobals())
beforeEach(() => setLocale('zh-CN'))
afterEach(() => {
  document.body.innerHTML = ''
})

const saved: unknown[] = []

function mount(initial: unknown = null) {
  ;(window as unknown as { innerWidth: number }).innerWidth = 1280
  saved.length = 0
  const Host = {
    components: { McpServerDialog },
    setup: () => ({ initial, onSave: (draft: unknown) => saved.push(draft) }),
    data: () => ({ open: true }),
    template: `<v-app><McpServerDialog v-model="open" :initial="initial" agent-name="芝士" :saving="false" error="" @save="onSave" /></v-app>`,
  }
  return render(Host, { global: { plugins: [createVuetify({ components, directives }), i18n] } })
}

const button = (label: string) =>
  [...document.querySelectorAll<HTMLButtonElement>('button')].find((b) => b.textContent?.trim() === label)!
const addButton = () => button('添加')

async function chooseCommand() {
  const radio = [...document.querySelectorAll<HTMLInputElement>('input[type="radio"]')].find(
    (r) => r.value === 'command'
  )!
  await fireEvent.click(radio)
  radio.checked = true
  await fireEvent.input(radio)
  await fireEvent.change(radio)
  await waitFor(() => expect(document.body.textContent).toContain('命令'))
}

async function fill(label: string, value: string) {
  const field = [...document.querySelectorAll('label')].find((l) => l.textContent?.trim() === label)
  const input = document.getElementById(field!.getAttribute('for')!) as HTMLInputElement
  await fireEvent.update(input, value)
}

it('adds a command to run on the project machine, with its environment', async () => {
  mount()
  await waitFor(() => expect(document.body.textContent).toContain('在项目机器上运行'))
  await chooseCommand()
  await fill('名称', 'pg')
  await fill('命令', 'npx -y server-postgres ${DATABASE_URL}')
  await fill('环境变量（可选）', 'PGSSLMODE=require\n\nPGPASSWORD=${PG_PASSWORD}')
  await fireEvent.click(addButton())
  expect(saved).toEqual([
    {
      kind: 'command',
      name: 'pg',
      command: 'npx -y server-postgres ${DATABASE_URL}',
      env: { PGSSLMODE: 'require', PGPASSWORD: '${PG_PASSWORD}' },
    },
  ])
})

it('an environment line that is not NAME=value keeps the command from being added', async () => {
  mount()
  await waitFor(() => expect(document.body.textContent).toContain('在项目机器上运行'))
  await chooseCommand()
  await fill('名称', 'pg')
  await fill('命令', 'uvx pg-mcp')
  await fill('环境变量（可选）', 'just some text')
  await fireEvent.click(addButton())
  expect(saved).toEqual([])
})

it('editing a command keeps it a command and shows what it runs', async () => {
  mount({ kind: 'command', name: 'pg', command: 'uvx pg-mcp', env: { MODE: 'read' } })
  await waitFor(() => expect(document.body.textContent).toContain('编辑 pg'))
  await fireEvent.click(button('保存'))
  expect(saved).toEqual([{ kind: 'command', name: 'pg', command: 'uvx pg-mcp', env: { MODE: 'read' } }])
})
