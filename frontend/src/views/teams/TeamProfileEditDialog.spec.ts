import type { Component } from 'vue'
import type { Team } from '@/types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const update = vi.fn()
const uploadAvatar = vi.fn()
vi.mock('@/network/api/teams', () => ({ TeamsApi: { update: (...a: unknown[]) => update(...a) } }))
vi.mock('@/network/api/avatars', () => ({ AvatarsApi: { createAvatar: (...a: unknown[]) => uploadAvatar(...a) } }))

import TeamProfileEditDialog from './TeamProfileEditDialog.vue'

import { setLocale } from '@/i18n'
import { BusinessError } from '@/network/types/error'

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

function mount(teamData: Team = team()) {
  return render(TeamProfileEditDialog as unknown as Component, {
    props: { modelValue: true, team: teamData },
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
  update.mockReset().mockResolvedValue({ data: { team: team({ name: '芝士社', intro: '新的介绍' }) } })
  uploadAvatar.mockReset().mockResolvedValue({ data: { avatarId: 77 } })
  URL.createObjectURL = vi.fn(() => 'blob:avatar-preview')
})
afterEach(cleanup)

describe('editing what a team looks like', () => {
  it('starts from the team as it is now', async () => {
    mount()
    await waitFor(() => expect((screen.getByLabelText('团队名称') as HTMLInputElement).value).toBe('Cheese 核心组'))
    expect((screen.getByLabelText('团队介绍') as HTMLTextAreaElement).value).toBe('一起把芝士做完')
  })

  it('saves the new name and intro and hands the saved team back', async () => {
    const view = mount()
    const name = await screen.findByLabelText('团队名称')
    await fireEvent.update(name, ' 芝士社 ')
    await fireEvent.update(screen.getByLabelText('团队介绍'), '新的介绍')
    await fireEvent.click(screen.getByRole('button', { name: '保存' }))

    // 名字两头带空格是人打字时会留下的东西，别让它变成一个「撞名」的假警报。
    await waitFor(() => expect(update).toHaveBeenCalledWith(7, { name: '芝士社', intro: '新的介绍' }))
    expect(view.emitted('updated')).toEqual([[team({ name: '芝士社', intro: '新的介绍' })]])
    await waitFor(() => expect(view.emitted('update:modelValue')).toEqual([[false]]))
  })

  it('uploads a new avatar first and sends the id it got back', async () => {
    mount()
    const file = new File(['image'], 'avatar.png', { type: 'image/png' })
    await fireEvent.change(document.querySelector('input[type="file"]')!, { target: { files: [file] } })
    await fireEvent.click(screen.getByRole('button', { name: '保存' }))

    await waitFor(() => expect(uploadAvatar).toHaveBeenCalledWith(file))
    await waitFor(() =>
      expect(update).toHaveBeenCalledWith(7, { name: 'Cheese 核心组', intro: '一起把芝士做完', avatarId: 77 })
    )
  })

  it('keeps the current avatar when the picture was not touched', async () => {
    mount()
    await fireEvent.click(await screen.findByRole('button', { name: '保存' }))
    await waitFor(() => expect(update).toHaveBeenCalledWith(7, { name: 'Cheese 核心组', intro: '一起把芝士做完' }))
    expect(uploadAvatar).not.toHaveBeenCalled()
  })

  it('points at the name when another team already holds it', async () => {
    // 后端对撞名给的是 409 + data.field=name。这是用户自己能修的错 —— 换一个名字
    // 就行 —— 所以要说在名字那一格上，不能掉进「保存失败，请稍后重试」。
    update.mockRejectedValue(
      new BusinessError('taken', 409, { name: 'Conflict', message: 'taken', data: { field: 'name' } })
    )
    const view = mount()
    await fireEvent.click(await screen.findByRole('button', { name: '保存' }))

    await screen.findByText('这个名称已被占用，换一个试试')
    expect(screen.queryByText('保存失败，请稍后重试')).toBeNull()
    expect(view.emitted('updated')).toBeUndefined()
  })

  it('clears the name complaint once the person edits the name', async () => {
    update.mockRejectedValueOnce(new BusinessError('taken', 409, { name: 'Conflict', message: 'taken', data: {} }))
    mount()
    await fireEvent.click(await screen.findByRole('button', { name: '保存' }))
    await screen.findByText('这个名称已被占用，换一个试试')

    await fireEvent.update(screen.getByLabelText('团队名称'), '芝士社')
    await waitFor(() => expect(screen.queryByText('这个名称已被占用，换一个试试')).toBeNull())
  })

  it('will not send a nameless team', async () => {
    mount()
    await fireEvent.update(await screen.findByLabelText('团队名称'), '   ')
    await fireEvent.click(screen.getByRole('button', { name: '保存' }))

    await screen.findByText('请输入团队名称')
    expect(update).not.toHaveBeenCalled()
  })

  it('says so when the save fails for a reason the person cannot fix', async () => {
    update.mockRejectedValue(new Error('offline'))
    mount()
    await fireEvent.click(await screen.findByRole('button', { name: '保存' }))
    await screen.findByText('保存失败，请稍后重试')
  })
})
