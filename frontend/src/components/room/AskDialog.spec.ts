/** 带选项提问：房间里的人写一个问题和两到四个选项，发出去的就是写下的那些。
 *
 *   1. 交上去的是去掉首尾空白的问题和选项；
 *   2. 选项空着、重复、或者没写问题，发不出去——答案按选项的字认，两个一样的选项
 *      点了分不出是哪一个；
 *   3. 选项最多四个、最少两个；
 *   4. 发成了框关上；发失败了框不关，原因写在框里，写下的字都还在。
 */
import type { Component } from 'vue'

import { ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import AskDialog from './AskDialog.vue'

import { setLocale } from '@/i18n'

const Dialog = AskDialog as unknown as Component

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

beforeEach(() => setLocale('zh-CN'))

const submitted = vi.fn()

/** 发出去的那条路由外面交进来，像房间那一层一样。 */
function mount() {
  submitted.mockReset()
  submitted.mockResolvedValue(undefined)
  const open = ref(true)
  const Host = {
    setup() {
      return { open, submitted }
    },
    components: { Dialog },
    template: `<div><Dialog v-model="open" :post="submitted" /></div>`,
  }
  render(Host as unknown as Component, { global: { plugins: [vuetify] } })
  return open
}

async function type(label: string, value: string) {
  await fireEvent.update(await screen.findByLabelText(label), value)
}

function sendButton(): HTMLButtonElement {
  return screen.getByRole('button', { name: '发送' }) as HTMLButtonElement
}

describe('带选项提问', () => {
  it('交上去的是写下的问题和选项', async () => {
    mount()
    await type('问题', '  周会挪到周四吗 ')
    await type('选项 1', '行')
    await type('选项 2', ' 不行 ')
    await fireEvent.click(sendButton())

    expect(submitted).toHaveBeenCalledWith('周会挪到周四吗', ['行', '不行'])
  })

  it('没写问题、选项空着或重复，发不出去', async () => {
    mount()
    await type('选项 1', '行')
    await type('选项 2', '不行')
    expect(sendButton().disabled).toBe(true)

    await type('问题', '周会挪到周四吗')
    expect(sendButton().disabled).toBe(false)

    await type('选项 2', '行')
    expect(sendButton().disabled).toBe(true)
    expect(screen.getByText('和前面的选项重复')).toBeTruthy()

    await type('选项 2', '')
    expect(sendButton().disabled).toBe(true)
    await fireEvent.click(sendButton())
    expect(submitted).not.toHaveBeenCalled()
  })

  it('选项最多四个，删到两个就不能再删', async () => {
    mount()
    expect(screen.queryByRole('button', { name: '删除选项' })).toBeNull()
    await fireEvent.click(screen.getByRole('button', { name: '添加选项' }))
    await fireEvent.click(screen.getByRole('button', { name: '添加选项' }))
    expect(await screen.findByLabelText('选项 4')).toBeTruthy()
    expect(screen.queryByRole('button', { name: '添加选项' })).toBeNull()

    await type('问题', '先做哪个')
    for (const [i, text] of ['甲', '乙', '丙', '丁'].entries()) await type(`选项 ${i + 1}`, text)
    await fireEvent.click(screen.getAllByRole('button', { name: '删除选项' })[0])
    await fireEvent.click(screen.getAllByRole('button', { name: '删除选项' })[0])
    expect(screen.queryByRole('button', { name: '删除选项' })).toBeNull()

    await fireEvent.click(sendButton())
    expect(submitted).toHaveBeenCalledWith('先做哪个', ['丙', '丁'])
  })

  it('发成了框关上', async () => {
    const open = mount()
    await type('问题', '周会挪到周四吗')
    await type('选项 1', '行')
    await type('选项 2', '不行')
    await fireEvent.click(sendButton())

    await waitFor(() => expect(open.value).toBe(false))
  })

  it('发失败了，框不关，原因写在框里，写下的字都还在', async () => {
    const open = mount()
    submitted.mockRejectedValue(new Error('只有房间成员能在这里提问'))
    await type('问题', '周会挪到周四吗')
    await type('选项 1', '行')
    await type('选项 2', '不行')
    await fireEvent.click(sendButton())

    expect(await screen.findByText('只有房间成员能在这里提问')).toBeTruthy()
    expect(open.value).toBe(true)
    expect((screen.getByLabelText('问题') as HTMLInputElement).value).toBe('周会挪到周四吗')
    expect((screen.getByLabelText('选项 2') as HTMLInputElement).value).toBe('不行')
  })
})
