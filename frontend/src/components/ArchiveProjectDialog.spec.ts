/** ArchiveProjectDialog：把项目名打一遍才放行，名字打对了往外 emit `archive`；被拒时
 *  理由留在弹窗里，重开时清掉；一次归档结束而没有理由就自己关窗。发请求、刷清单、离开
 *  项目在 `composables/useProjectArchive.ts`（见 useProjectArchive.spec.ts）。 */
import type { Component } from 'vue'

import { nextTick, ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest'

import ArchiveProjectDialog from './ArchiveProjectDialog.vue'

import { setLocale } from '@/i18n'

const Dialog = ArchiveProjectDialog as unknown as Component
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
  if (!globalThis.visualViewport) {
    ;(globalThis as unknown as { visualViewport: unknown }).visualViewport = {
      width: 1024,
      height: 768,
      offsetLeft: 0,
      offsetTop: 0,
      scale: 1,
      addEventListener() {},
      removeEventListener() {},
      dispatchEvent: () => false,
    }
  }
})

afterEach(cleanup)

beforeEach(() => setLocale('zh-CN'))

async function mount() {
  const open = ref(false)
  const archiving = ref(false)
  const error = ref('')
  let archived = 0
  const Host = {
    setup: () => ({ open, archiving, error, onArchive: () => (archived += 1) }),
    components: { Dialog },
    template: `<div><button type="button" data-testid="open" @click="open = true">open</button><Dialog v-model="open" project-name="毕业设计" :archiving="archiving" :error="error" @archive="onArchive" /></div>`,
  }
  const utils = render(Host as unknown as Component, { global: { plugins: [vuetify] } })
  await fireEvent.click(utils.getByTestId('open'))
  return { ...utils, open, archiving, error, archived: () => archived }
}

function archiveButton(): HTMLButtonElement {
  return screen.getByRole('button', { name: '归档' }) as HTMLButtonElement
}

async function typeName(name = '毕业设计') {
  await fireEvent.update(await screen.findByLabelText('输入项目名称「毕业设计」确认'), name)
}

describe('ArchiveProjectDialog', () => {
  it('项目名没打对之前不能归档', async () => {
    const { archived } = await mount()
    expect(archiveButton().disabled).toBe(true)
    await typeName('毕业')
    expect(archiveButton().disabled).toBe(true)
    await fireEvent.click(archiveButton())
    expect(archived()).toBe(0)
  })

  it('打对名字之后往外要一次归档；归档结束而没有理由就关窗', async () => {
    const { open, archiving, archived } = await mount()
    await typeName()
    await fireEvent.click(archiveButton())
    expect(archived()).toBe(1)
    archiving.value = true
    await nextTick()
    archiving.value = false
    await nextTick()
    expect(open.value).toBe(false)
  })

  it('被拒时弹窗不关，理由说在弹窗里；重开时清掉', async () => {
    const { open, archiving, error } = await mount()
    await typeName()
    await fireEvent.click(archiveButton())
    archiving.value = true
    await nextTick()
    error.value = '只有项目所有者能归档或取消归档项目'
    archiving.value = false
    expect(await screen.findByText('只有项目所有者能归档或取消归档项目')).toBeTruthy()
    expect(open.value).toBe(true)

    open.value = false
    await nextTick()
    open.value = true
    await waitFor(() => expect(screen.queryByText('只有项目所有者能归档或取消归档项目')).toBeNull())
  })
})
