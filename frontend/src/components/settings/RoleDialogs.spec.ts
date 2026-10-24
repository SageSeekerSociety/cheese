// 新增和删除角色的两个对话框（#4795）。错了也照样渲染、却会让人做错事的几条：
//   1. 新增：照另一个角色复制时必须起名；从导师模板开始可以不起名
//   2. 新增：项目已有导师角色时不再提供导师模板
//   3. 删除：还有人在用时，要选他们改为什么角色，选的角色随删除一起交出去
//   4. 删除：不是所有者就不能把人改成管理员
import type { ProjectRoleRow } from '@/api'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it } from 'vitest'

import RoleCreateDialog from './RoleCreateDialog.vue'
import RoleDeleteDialog from './RoleDeleteDialog.vue'

import i18n, { setLocale } from '@/i18n'

const role = (key: string, extra: Partial<ProjectRoleRow>): ProjectRoleRow => ({
  key,
  name: null,
  builtin: null,
  template: null,
  permissions: [],
  defaults: null,
  holders: 0,
  ...extra,
})
const ADMIN = role('admin', { builtin: 'admin' })
const MEMBER = role('member', { builtin: 'member' })
// i18n-data: a role name the project chose, rendered as its user data
const TESTER = role('r-tester', { name: '内测用户', holders: 12 })

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
  if (!('visualViewport' in globalThis)) {
    ;(globalThis as unknown as { visualViewport: unknown }).visualViewport = {
      width: 1024,
      height: 768,
      offsetLeft: 0,
      offsetTop: 0,
      scale: 1,
      addEventListener() {},
      removeEventListener() {},
    }
  }
  if (!globalThis.devicePixelRatio) {
    Object.defineProperty(globalThis, 'devicePixelRatio', { configurable: true, value: 1 })
  }
})
beforeEach(() => setLocale('zh-CN'))

async function choose(dialog: HTMLElement, label: string, option: string) {
  await fireEvent.mouseDown(within(dialog).getByLabelText(label).closest('.v-field') as Element)
  await waitFor(() => expect(document.body.querySelectorAll('.v-overlay .v-list-item').length).toBeGreaterThan(0))
  const item = Array.from(document.body.querySelectorAll('.v-overlay .v-list-item')).find((el) =>
    el.textContent?.trim().startsWith(option)
  )
  await fireEvent.click(item as Element)
}

function options(): string[] {
  return Array.from(document.body.querySelectorAll('.v-overlay .v-list-item'), (el) => el.textContent?.trim() ?? '')
}

describe('新增角色', () => {
  function mount(roles: ProjectRoleRow[]) {
    return render(RoleCreateDialog, {
      props: { modelValue: true, roles, saving: false, error: null },
      global: { plugins: [vuetify, i18n] },
    })
  }

  it('照另一个角色复制时要起名，起了名才交出去', async () => {
    const { emitted } = mount([ADMIN, MEMBER])
    const dialog = await screen.findByRole('dialog')
    const create = within(dialog).getByRole('button', { name: '新增' })
    expect(create.hasAttribute('disabled')).toBe(true)
    await fireEvent.update(within(dialog).getByLabelText('角色名称'), '助教')
    await fireEvent.click(create)
    expect(emitted('create')).toEqual([['助教', 'member']])
  })

  it('从导师模板开始可以不起名', async () => {
    const { emitted } = mount([ADMIN, MEMBER])
    const dialog = await screen.findByRole('dialog')
    await choose(dialog, '权限复制自', '导师')
    await fireEvent.click(within(dialog).getByRole('button', { name: '新增' }))
    expect(emitted('create')).toEqual([[null, 'template:mentor']])
  })

  it('项目已有导师角色时不再提供导师模板', async () => {
    mount([ADMIN, MEMBER, role('r-mentor', { template: 'mentor' })])
    const dialog = await screen.findByRole('dialog')
    await fireEvent.mouseDown(within(dialog).getByLabelText('权限复制自').closest('.v-field') as Element)
    await waitFor(() => expect(options().length).toBe(3))
    expect(options().some((o) => o.includes('模板'))).toBe(false)
  })
})

describe('删除角色', () => {
  function mount(makesAdmins: boolean, target = TESTER) {
    return render(RoleDeleteDialog, {
      props: {
        modelValue: true,
        role: target,
        roles: [ADMIN, MEMBER, TESTER],
        makesAdmins,
        saving: false,
        error: null,
      },
      global: { plugins: [vuetify, i18n] },
    })
  }

  it('还有人在用时，选的角色随删除一起交出去', async () => {
    const { emitted } = mount(true)
    const dialog = await screen.findByRole('dialog')
    await choose(dialog, '这个角色的 12 人改为', '管理员')
    await fireEvent.click(within(dialog).getByRole('button', { name: '删除' }))
    expect(emitted('delete')).toEqual([['admin']])
  })

  it('没人在用就直接删，不问改为什么', async () => {
    const { emitted } = mount(true, { ...TESTER, holders: 0 })
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).queryByLabelText(/改为/)).toBeNull()
    await fireEvent.click(within(dialog).getByRole('button', { name: '删除' }))
    expect(emitted('delete')).toEqual([[undefined]])
  })

  it('不是所有者就不能把人改成管理员', async () => {
    mount(false)
    const dialog = await screen.findByRole('dialog')
    await fireEvent.mouseDown(within(dialog).getByLabelText('这个角色的 12 人改为').closest('.v-field') as Element)
    await waitFor(() => expect(options().length).toBeGreaterThan(0))
    expect(options()).toEqual(['成员'])
  })
})
