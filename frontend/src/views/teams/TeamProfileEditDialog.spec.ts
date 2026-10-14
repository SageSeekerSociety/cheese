// 改团队资料这一个弹窗自己的事：打开时从小队现在的样子起手，填好了把三样报上去。
// 真去传头像、PATCH、判撞名和没权限的在外面（`composables/useTeamNav.spec.ts`）。
import type { Component } from 'vue'
import type { Team } from '@/types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import TeamProfileEditDialog from './TeamProfileEditDialog.vue'

import { setLocale } from '@/i18n'

function team(overrides: Partial<Team> = {}): Team {
  return {
    id: 7,
    handle: 'cheese-core',
    name: 'Cheese 核心组',
    intro: '一起把芝士做完',
    avatarId: 3,
    owner: { id: 1, nickname: '队长' } as Team['owner'],
    admins: { total: 0, examples: [] },
    members: { total: 1, examples: [] },
    role: 'OWNER',
    visibility: 'public',
    ...overrides,
  }
}

function mount(props: Record<string, unknown> = {}) {
  return render(TeamProfileEditDialog as unknown as Component, {
    props: { modelValue: true, team: team(), ...props },
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

beforeAll(() => {
  // Vuetify 的浮层（v-dialog 就是一个 VOverlay）会去读 `visualViewport` 和
  // `ResizeObserver`，happy-dom 里这两样都没有，不补上弹窗根本不渲染 ——
  // 断言会以为「弹窗没打开」。
  vi.stubGlobal('visualViewport', {
    width: 1024,
    height: 768,
    offsetLeft: 0,
    offsetTop: 0,
    scale: 1,
    addEventListener() {},
    removeEventListener() {},
  })
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  )
})

beforeEach(() => {
  setLocale('zh-CN')
  URL.createObjectURL = vi.fn(() => 'blob:avatar-preview')
})
afterEach(cleanup)

describe('改团队资料这一个弹窗', () => {
  it('打开时从小队现在的样子起手', async () => {
    mount()
    await waitFor(() => expect((screen.getByLabelText('团队名称') as HTMLInputElement).value).toBe('Cheese 核心组'))
    expect((screen.getByLabelText('团队介绍') as HTMLTextAreaElement).value).toBe('一起把芝士做完')
  })

  it('按了保存就把人填的三样原样报上去', async () => {
    const view = mount()
    await fireEvent.update(await screen.findByLabelText('团队名称'), ' 芝士社 ')
    await fireEvent.update(screen.getByLabelText('团队介绍'), '新的介绍')
    await fireEvent.click(screen.getByRole('button', { name: '保存' }))

    // 两头带空格照原样报：裁不裁是外面的事，这一件只管收。
    expect(view.emitted('save')).toEqual([[{ name: ' 芝士社 ', intro: '新的介绍', avatarFile: undefined }]])
  })

  it('换过图就把那个文件一起报上去', async () => {
    const view = mount()
    const file = new File(['image'], 'avatar.png', { type: 'image/png' })
    await fireEvent.change(document.querySelector('input[type="file"]')!, { target: { files: [file] } })
    await fireEvent.click(await screen.findByRole('button', { name: '保存' }))

    const saved = view.emitted('save')![0][0] as { avatarFile?: File }
    expect(saved.avatarFile?.name).toBe('avatar.png')
  })

  it('正在保存时按不动第二下，也关不掉', async () => {
    const view = mount({ busy: true })
    vi.spyOn(console, 'error').mockImplementation(() => {})
    await fireEvent.click(await screen.findByRole('button', { name: '保存' }))
    await fireEvent.click(screen.getByRole('button', { name: '取消' }))
    expect(view.emitted('save')).toBeUndefined()
    expect(view.emitted('update:modelValue')).toBeUndefined()
  })

  it('外面给回来的两句报错照原样画出来', async () => {
    mount({ error: '保存失败，请稍后重试', nameError: '这个名称已被占用，换一个试试' })
    expect(await screen.findByText('保存失败，请稍后重试')).toBeTruthy()
    expect(screen.getByText('这个名称已被占用，换一个试试')).toBeTruthy()
  })

  it('人在名字那一格里一动就告诉外面', async () => {
    const view = mount({ nameError: '这个名称已被占用，换一个试试' })
    await fireEvent.update(await screen.findByLabelText('团队名称'), '芝士社')
    await waitFor(() => expect(view.emitted('clearErrors')).toEqual([[]]))
  })

  it('每打开一次都从小队现在的样子重新起手', async () => {
    const view = mount({ modelValue: false })
    await view.rerender({ modelValue: true })
    await fireEvent.update(await screen.findByLabelText('团队名称'), '乱改的')
    await view.rerender({ modelValue: false })
    await view.rerender({ modelValue: true })
    await waitFor(() => expect((screen.getByLabelText('团队名称') as HTMLInputElement).value).toBe('Cheese 核心组'))
  })
})
