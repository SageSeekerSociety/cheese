/**
 * 成员管理（`/admin/members`）：两份名单分开画、搜账号走服务端、移出要确认。
 *
 * 这一页没有权限门 —— 门在外壳上（`AdminLayout.spec.ts`）。它自己只管名单。
 *
 * 每条都钉住一个**会静默坏掉**的地方：
 *
 * 1. 根那几行**没有「移出」**。它是服务端的规则（`AdminService.remove_admin` 回 409），
 *    但页面必须画对 —— 把两块合成一个列表、每行都画按钮的话，人按下去才知道不行，
 *    而那时他已经在怀疑是不是自己点错了。
 * 2. 搜账号是**服务端搜**：输入框里的字要打到 `/admin/users` 上，而且**只打一次**。
 *    一次拉回来本地过滤的实现照样「能用」，只是真人搜不到人（这个部署 1199 个账号）；
 *    不防抖的实现在一次搜索里会把每个前缀都发出去。
 * 3. 移出**先确认再发请求**：这个动作当场决定谁能看别人的私密反馈，而按钮就挨着一个
 *    名字。
 *
 * 没测到的一条，说清楚省得下次有人以为它被覆盖了：**从候选里选中再点「添加」**这一步
 * 在 happy-dom 里做不到 —— `v-autocomplete` 的候选画在浮层菜单里，而浮层在这个环境
 * 里不展开（点是 `focus`/`click` 驱动的，试过都不行）。`addWithin` 那条路（含被拒时
 * 显示服务端原话、框不关）在**预览和 e2e 的真浏览器**里点，不在这一份里。
 */
import type { Component } from 'vue'

import { createRouter, createWebHashHistory } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const listPlatformAdmins = vi.fn()
const searchAdminCandidates = vi.fn()
const addPlatformAdmin = vi.fn()
const removePlatformAdmin = vi.fn()

vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    listPlatformAdmins: (...a: unknown[]) => listPlatformAdmins(...a),
    searchAdminCandidates: (...a: unknown[]) => searchAdminCandidates(...a),
    addPlatformAdmin: (...a: unknown[]) => addPlatformAdmin(...a),
    removePlatformAdmin: (...a: unknown[]) => removePlatformAdmin(...a),
  }
})

import AdminMembersPage from './AdminMembersPage.vue'

const ROSTER = {
  root: ['andy', 'wangchangxin'],
  added: [{ handle: 'pengwenbo', added_by_handle: 'andy', created_at: '2026-09-19T10:00:00Z' }],
}

function mountPage() {
  const router = createRouter({
    history: createWebHashHistory(),
    routes: [{ path: '/:pathMatch(.*)*', component: { template: '<div />' } }],
  })
  const vuetify = createVuetify({ components, directives })
  const Wrapper = {
    components: { AdminMembersPage },
    template: '<v-app><AdminMembersPage /></v-app>',
  }
  return render(Wrapper as unknown as Component, {
    global: { plugins: [vuetify, createPinia(), router] },
  })
}

// happy-dom 里没有 `visualViewport`，而 Vuetify 的浮层定位要读它（`locationStrategies`），
// 于是对话框**一打开**就在那儿抛 ReferenceError，测试里表现为「点了按钮什么都没发生」。
// 补一个最小的空壳就够它算完。
//
// 补壳而不是像 `views/tasks/detail/Submit.spec.ts` 那样把 `VDialog` stub 掉：那一页
// 的对话框只是提交表单的一个壳，而这一页要看的东西都在对话框里（开关、搜索、确认），
// stub 之后内容恒在 DOM 里，「框开没开」就再也测不出来了。
beforeAll(() => {
  Object.defineProperty(window, 'visualViewport', {
    configurable: true,
    value: {
      width: 1024,
      height: 768,
      offsetLeft: 0,
      offsetTop: 0,
      pageLeft: 0,
      pageTop: 0,
      scale: 1,
      addEventListener() {},
      removeEventListener() {},
    },
  })
})

beforeEach(() => {
  listPlatformAdmins.mockReset().mockResolvedValue(ROSTER)
  searchAdminCandidates.mockReset().mockResolvedValue({ items: [] })
  addPlatformAdmin.mockReset()
  removePlatformAdmin.mockReset()
})

describe('成员管理', () => {
  it('两份名单分开画：根那几行没有「移出」', async () => {
    const { findByText, findAllByText, getAllByText } = mountPage()

    expect(await findByText('部署配置里的根管理员')).toBeTruthy()
    expect(await findByText('页面上添加的')).toBeTruthy()
    expect(await findByText('pengwenbo')).toBeTruthy()
    expect(await findByText('andy')).toBeTruthy()
    // 页面上加的那一行带出处：谁加的、什么时候。
    expect(await findAllByText(/andy 加的/)).toHaveLength(1)

    // 整页只有一个「移出」，在 `pengwenbo` 那一行。根那两行（andy、wangchangxin）
    // 每个都画一个的话这里会是三个。
    expect(getAllByText('移出')).toHaveLength(1)
  })

  it('搜账号打到服务端，而且只打一次', async () => {
    const { findByText, findByPlaceholderText } = mountPage()

    await fireEvent.click(await findByText('添加管理员'))
    const input = await findByPlaceholderText('输入 handle 或昵称')
    // 连打三个字：防抖之后只该有一次请求，问的是**最后那串**。
    await fireEvent.update(input, '池')
    await fireEvent.update(input, '池若')
    await fireEvent.update(input, '池若彤')

    // 本地过滤的实现一个请求都不发；逐字发的实现留下三条。
    await vi.waitFor(() => expect(searchAdminCandidates).toHaveBeenCalledTimes(1))
    expect(searchAdminCandidates).toHaveBeenCalledWith('池若彤')

    // 这一条到此为止：**「答案要画出来」那半在这里测不了**。`v-autocomplete` 的候选项
    // 在菜单里，而 happy-dom 里那个菜单根本不挂载（`fireEvent.update` / `focus` 都试过，
    // 连同 handle 能命中的 ASCII 查询也取不到候选项），所以任何 `findByText('chiruotong')`
    // 都必然红、红在环境上而不是代码上。那半的判据在预览里（真浏览器、真交互）：
    // 按昵称搜得到人这件事，只有真跑一次才算数。
    //
    // 这里**少了**这一条，是为什么 `:no-filter="true"` 曾经漏掉：服务端按昵称查、
    // 组件再拿同一个串去比 `item-title`（= handle），查到的那一行被组件自己筛掉，
    // 而接口、fixture、上面这几条断言三处各自看都对。
  })

  it('移出要先确认，确认之后才发请求', async () => {
    removePlatformAdmin.mockResolvedValue({ root: ROSTER.root, added: [], removed: true })

    const { findByText, findAllByText, findAllByRole, queryByText } = mountPage()

    await fireEvent.click(await findByText('移出'))
    expect(removePlatformAdmin).not.toHaveBeenCalled()
    // 确认那一行**带名字**：只说「确定删除吗」而实际删掉的是名单上另一个人的话，
    // 按按钮的人没有任何办法发现自己按错了。这里按「那句话在」来认它 —— 名字本身
    // 包在 `<strong>` 里，是一个独立元素，跟周围那几个字凑不成一个 matcher。
    expect(await findByText(/移出名单？他马上看不到私密反馈/)).toBeTruthy()
    // 名字出现两处：名单那一行 + 确认框里那个 `<strong>`。
    expect(await findAllByText('pengwenbo')).toHaveLength(2)

    // 行上和确认框里各有一个「移出」，同名。按**浮层里那个**来挑，不靠顺序 ——
    // 对话框的内容在 DOM 里的位置由浮层决定，跟页面的先后没有关系。
    const buttons = await findAllByRole('button', { name: '移出' })
    const confirm = buttons.find((one) => one.closest('.v-overlay'))
    expect(confirm).toBeTruthy()
    await fireEvent.click(confirm as HTMLElement)

    expect(removePlatformAdmin).toHaveBeenCalledWith('pengwenbo')
    expect(await findByText('已移出：pengwenbo')).toBeTruthy()
    // 名单当场变：那一行没了，剩下的是空态那句话。
    expect(queryByText('pengwenbo')).toBeNull()
    expect(await findByText(/还没有在页面上加过管理员/)).toBeTruthy()
  })
})
