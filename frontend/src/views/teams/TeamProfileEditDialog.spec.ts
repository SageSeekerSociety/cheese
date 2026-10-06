// 小队资料弹窗只手写一张表：填完（名字不能空是自己判的）把草稿整份报上去。传图、
// PATCH、报错落到哪一格都在外面，所以这里断言的是「报上去的是什么」和「外面说的错话
// 画在哪里」。真正发请求那一半见 `views/home/HomeSidebar.spec.ts`（容器那一侧）。
import type { Component } from 'vue'
import type { Team } from '@/types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import TeamProfileEditDialog from './TeamProfileEditDialog.vue'

import { setLocale } from '@/i18n'

interface TeamProfileDraft {
  name: string
  intro: string
  avatarFile?: File
}

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

function mount(extra: Record<string, unknown> = {}) {
  return render(TeamProfileEditDialog as unknown as Component, {
    props: { modelValue: true, team: team(), ...extra },
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

function drafts(view: { emitted: () => Record<string, unknown[] | undefined> }): TeamProfileDraft[] {
  return ((view.emitted().save ?? []) as unknown[][]).map((call) => call[0] as TeamProfileDraft)
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

describe('editing what a team looks like', () => {
  it('starts from the team as it is now', async () => {
    mount()
    await waitFor(() => expect((screen.getByLabelText('团队名称') as HTMLInputElement).value).toBe('Cheese 核心组'))
    expect((screen.getByLabelText('团队介绍') as HTMLTextAreaElement).value).toBe('一起把芝士做完')
  })

  it('hands the draft over, with the name trimmed', async () => {
    // 名字两头带空格是人打字时会留下的东西，别让它变成一个「撞名」的假警报。
    const view = mount()
    await fireEvent.update(await screen.findByLabelText('团队名称'), ' 芝士社 ')
    await fireEvent.update(screen.getByLabelText('团队介绍'), '新的介绍')
    await fireEvent.click(screen.getByRole('button', { name: '保存' }))

    await waitFor(() => expect(drafts(view)).toEqual([{ name: '芝士社', intro: '新的介绍' }]))
  })

  it('hands the picked picture over with the draft', async () => {
    const view = mount()
    const file = new File(['image'], 'avatar.png', { type: 'image/png' })
    await fireEvent.change(document.querySelector('input[type="file"]')!, { target: { files: [file] } })
    await fireEvent.click(screen.getByRole('button', { name: '保存' }))

    // 图片本身按内容比：测试里那个 File 和控件交上来的是两个实例，同一张图。
    await waitFor(() => expect(drafts(view)[0]?.avatarFile?.name).toBe('avatar.png'))
  })

  it('leaves the picture out when it was not touched', async () => {
    const view = mount()
    await fireEvent.click(await screen.findByRole('button', { name: '保存' }))

    await waitFor(() => expect(drafts(view).length).toBe(1))
    expect('avatarFile' in drafts(view)[0]).toBe(false)
  })

  it('points at the name when the outside says that name is taken, and forgets it once the person renames', async () => {
    // 后端对撞名给的是 409 + data.field=name。这是用户自己能修的错 —— 换一个名字
    // 就行 —— 所以要说在名字那一格上，不能掉进「保存失败，请稍后重试」。
    const view = mount({ nameError: '这个名称已被占用，换一个试试' })
    await fireEvent.click(await screen.findByRole('button', { name: '保存' }))
    await screen.findByText('这个名称已被占用，换一个试试')

    // 人一动手改名字，那句话说的是上一个名字，作废。
    await fireEvent.update(screen.getByLabelText('团队名称'), '芝士社')
    await waitFor(() => expect(screen.queryByText('这个名称已被占用，换一个试试')).toBeNull())

    // 再报一次同一个名字：外面那句话仍然成立，它再回来。
    await fireEvent.click(screen.getByRole('button', { name: '保存' }))
    await screen.findByText('这个名称已被占用，换一个试试')
    expect(drafts(view).length).toBe(2)
  })

  it('will not hand over a nameless team', async () => {
    const view = mount()
    await fireEvent.update(await screen.findByLabelText('团队名称'), '   ')
    await fireEvent.click(screen.getByRole('button', { name: '保存' }))

    await screen.findByText('填写团队名称')
    expect(view.emitted().save).toBeUndefined()
  })

  it('says so when the save fails for a reason the person cannot fix', async () => {
    mount({ error: '保存失败，请稍后重试' })
    await screen.findByText('保存失败，请稍后重试')
  })
})
