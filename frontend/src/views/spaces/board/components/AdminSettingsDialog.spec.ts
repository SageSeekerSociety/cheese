// 管理员设置弹窗：**只读**的一份名单。
//
// 它照原型的样做（标题 + 一句解释 + 名单 + 关闭），所以这里钉三件事：
//   1. 名单上的每个人都画出来，而且**角色标对**（所有者 / 管理员）—— 名字与角色
//      都是从 `Space.admins` 来的（调用方喂进来），弹窗自己不做任何判断；
//   2. 名单**没有第二次拼 owner**：所有者只出现一次（后端的 `list_admins` 已经把
//      OWNER 那条一起返回了，再拼一次就会列两遍）；
//   3. 它是只读的：屏幕上除了「关闭」没有第二个能点的东西。
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

import AdminSettingsDialog from './AdminSettingsDialog.vue'

import i18n, { setLocale } from '@/i18n'

// 弹窗是一个 VOverlay，而测试环境没有 visualViewport：不补上根本挂不起来，
// 测到的就成了「点开什么也没有」。
beforeAll(() => {
  vi.stubGlobal('visualViewport', {
    width: 1024,
    height: 768,
    offsetLeft: 0,
    offsetTop: 0,
    addEventListener() {},
    removeEventListener() {},
  })
})
afterAll(() => vi.unstubAllGlobals())

beforeAll(() => setLocale('zh-CN'))

const MANAGERS = [
  { person: { handle: 'caisongyang', name: '蔡松洋' }, role: 'OWNER' as const },
  { person: { handle: 'maxiaoyu', name: '马霄宇' }, role: 'ADMIN' as const },
]

async function mount() {
  const utils = render(AdminSettingsDialog, {
    props: { managers: MANAGERS, modelValue: true },
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
  await waitFor(() => expect(document.body.textContent).toContain('蔡松洋'))
  return utils
}

afterEach(cleanup)

describe('管理员设置弹窗', () => {
  it('把名单上的每个人连同角色画出来，所有者只出现一次', async () => {
    await mount()

    // 名字来自 props（调用方从 `Space.admins` 取的），弹窗自己不看接口。
    for (const name of ['蔡松洋', '马霄宇']) {
      expect(screen.getAllByText(name)).toHaveLength(1)
    }
    expect(screen.getByText('所有者')).toBeTruthy()
    expect(screen.getByText('管理员')).toBeTruthy()
  })

  it('是只读的：除了「关闭」没有别的操作', async () => {
    await mount()

    expect(screen.getAllByRole('button')).toHaveLength(1)
    expect(screen.getByRole('button').textContent).toContain('关闭')
  })

  it('点「关闭」把 v-model 落回 false', async () => {
    const utils = await mount()

    await fireEvent.click(screen.getByText('关闭'))

    expect(utils.emitted('update:modelValue')).toEqual([[false]])
  })
})
