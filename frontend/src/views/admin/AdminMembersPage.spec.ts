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
 *    名字。确认框正文是全仓第一处 `<i18n-t>`（handle 回显加粗、英文语序不同，句子
 *    碎片键被 i18n.md §3 禁掉）—— happy-dom 里插槽渲不渲染得出来，由「handle 出现
 *    两次 + 后果句整句在」那两条断言钉住。
 * 4. 主行显示**昵称**、handle 作次要信息，而且**同一个串不画两遍**（昵称为空或就是
 *    handle 时只剩一个串）；头像对读屏不可见（名字就在旁边）。
 * 5. 确认框关掉之后**焦点回到触发它的那颗按钮**：没有 activator 的 `VDialog` 自己
 *    不归还焦点，掉到 `body` 上键盘用户就得从头 Tab 回来。
 * 6. 头像那格画的是**这个人自己的图**（`/avatars/<id>`），没挑过的人画彩色首字母、而
 *    不是所有人共用的 `/avatars/default`。`avatar_id` 为 null 是「从没挑过」的判据，
 *    漏判时把 null 交给 `getAvatarUrl` 会回一张**所有没挑过头像的人共用**的脸 —— 一列
 *    头像变成同一张，比按 handle 派生的颜色更难把人分辨开，而分辨人正是头像唯一的活。
 * 7. 取不到的头像 URL 在**本次会话**里只问一次（`utils/avatarFailures`）。dev 的种子
 *    迁移只往 avatars 表写了行、一张图也没落盘，不记的话每次切回这一页，每个坏 id 都
 *    会再造一个 `<img>` 去撞一次必然 404 的请求。
 * 8. **账号状态与注册时间**：平台上没账号的行在状态列明画（正常行只画 `—`，不刷一列
 *    「正常」的噪音），注册时间经 `relTime` 渲染、没账号的画 `—`；`is_agent` 的行在
 *    who 列挂 agent 徽章。三种「死权限」（没账号/已注销、agent）以前完全不可见。
 * 9. **刷新时旧名单只压暗（busy）不换骨架**：手上有数据时再取数，骨架闪一下是比
 *    「旧内容多停半秒」更糟的手感（AdminGrid 的 busy 槽就是为这个存在的）。
 *
 * 没测到的一条，说清楚省得下次有人以为它被覆盖了：**从候选里选中再点「添加」**这一步
 * 在 happy-dom 里做不到 —— `v-autocomplete` 的候选画在浮层菜单里，而浮层在这个环境
 * 里不展开（点是 `focus`/`click` 驱动的，试过都不行）。`addWithin` 那条路（含被拒时
 * 显示服务端原话、框不关）在**预览和 e2e 的真浏览器**里点，不在这一份里。
 */
import type { PlatformAdminsPayload } from '@/api'
import type { Component } from 'vue'

import { nextTick } from 'vue'
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

import i18n, { setLocale } from '@/i18n'
import { relTime } from '@/lib/relTime'

const ANDY_REGISTERED = '2025-11-03T08:12:44Z'
const PENG_REGISTERED = '2026-01-04T10:00:00Z'
const BOT_REGISTERED = '2026-03-01T00:00:00Z'

// 两组现在是同一个行形状（`root` 不再是一串裸 handle）。这里特意让三行的
// nickname/avatar_id 各占一种情况：andy 两样都有、wangchangxin 两样都为 null
// （平台上没有这个账号）、pengwenbo 是页面上加的、带出处。新三格也各占一种：
// wangchangxin 没账号（has_account=false、注册时间 null）、cheese-bot 是 agent
// （只会从根配置混进来 —— 页面加人服务端拒 agent）、其余行正常。
const ROSTER: PlatformAdminsPayload = {
  root: [
    { handle: 'andy', nickname: '安迪', avatar_id: 7, has_account: true, registered_at: ANDY_REGISTERED, is_agent: false },
    { handle: 'wangchangxin', nickname: null, avatar_id: null, has_account: false, registered_at: null, is_agent: false },
    { handle: 'cheese-bot', nickname: null, avatar_id: null, has_account: true, registered_at: BOT_REGISTERED, is_agent: true },
  ],
  added: [
    {
      handle: 'pengwenbo',
      nickname: '彭文博',
      avatar_id: 3,
      added_by_handle: 'andy',
      created_at: '2026-09-19T10:00:00Z',
      has_account: true,
      registered_at: PENG_REGISTERED,
      is_agent: false,
    },
  ],
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
    global: { plugins: [vuetify, createPinia(), router, i18n] },
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
  // 页面文案全走 i18n，而这一组断的是中文词条；`navigator.language` 在 happy-dom 里
  // 是 `en-US` —— 不钉住语言，断的就成了英文词条（照 `AdminQueuePage.spec.ts`）。
  setLocale('zh-CN')
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
    // 主行是昵称，handle 是次要信息：两个串都在。
    expect(await findByText('安迪')).toBeTruthy()
    expect(await findByText('彭文博')).toBeTruthy()
    expect(await findByText('pengwenbo')).toBeTruthy()
    expect(await findByText('andy')).toBeTruthy()
    // `wangchangxin` 没有昵称（平台上没这个账号）：主行退回 handle，而且只画一次。
    expect(await findByText('wangchangxin')).toBeTruthy()
    // 页面上加的那一行带出处：谁加的、什么时候。
    expect(await findAllByText(/andy 加的/)).toHaveLength(1)

    // 整页只有一个「移出」，在 `pengwenbo` 那一行。根那三行（andy、wangchangxin、
    // cheese-bot）每个都画一个的话这里会是四个。
    expect(getAllByText('移出')).toHaveLength(1)
  })

  it('昵称和 handle 不相同时，昵称在前、handle 作次要信息；昵称就是 handle 时只画一遍', async () => {
    const { findByText, findAllByText } = mountPage()
    expect(await findByText('安迪')).toBeTruthy()
    // 昵称和 handle 不同：昵称是主行，「安迪」不出现第二次。
    expect(await findAllByText('安迪')).toHaveLength(1)
    expect(await findAllByText('andy')).toHaveLength(1)

    // 另一种「只有一个串可画」：昵称本来就是 handle。画两遍的话这里会是 2。
    listPlatformAdmins.mockResolvedValue({
      root: [
        {
          handle: 'samename',
          nickname: 'samename',
          avatar_id: null,
          has_account: true,
          registered_at: ANDY_REGISTERED,
          is_agent: false,
        },
      ],
      added: [],
    })
    const again = mountPage()
    expect(await again.findByText('samename')).toBeTruthy()
    expect(await again.findAllByText('samename')).toHaveLength(1)
  })

  it('名单里的头像是装饰：对读屏不可见', async () => {
    const { container, findByText } = mountPage()
    await findByText('andy')

    const avatars = container.querySelectorAll('.fb-avatar')
    expect(avatars.length).toBeGreaterThan(0)
    avatars.forEach((el) => {
      // 没有 `aria-hidden` 的话，读屏会把头像当成一个无名图形挨个念出来。
      expect(el.closest('[aria-hidden="true"]')).toBeTruthy()
    })
  })

  it('挑过头像的人画各自的 /avatars/<id>，没挑过的人画彩色首字母而不是 /avatars/default', async () => {
    const { container, findByText } = mountPage()
    await findByText('安迪')

    const rowOf = (needle: string) =>
      Array.from(container.querySelectorAll('tr.am__row')).find((r) => r.textContent?.includes(needle))

    // 挑过的人：这一行的图指向**他自己那个 id**。两行各断言一次，才排得掉「某一列
    // 画对了、其余行画成同一张固定图」这种半对 —— 传错 `avatarId`（漏传、传成 handle、
    // 或哪一行写死一个常量）在这两条里总有一条会红。
    const andyImg = rowOf('安迪')?.querySelector('img')
    expect(andyImg, '挑过头像的人这一行该画 <img>，不是彩色首字母').not.toBeNull()
    expect(andyImg?.getAttribute('src')).toMatch(/\/avatars\/7$/)
    const pengImg = rowOf('彭文博')?.querySelector('img')
    expect(pengImg, '挑过头像的人这一行该画 <img>，不是彩色首字母').not.toBeNull()
    expect(pengImg?.getAttribute('src')).toMatch(/\/avatars\/3$/)

    // 没挑过的人（`avatar_id` 为 null）：**必须**走彩色首字母。把 null 交给
    // `getAvatarUrl` 会回 `/avatars/default`，那是所有没挑过头像的人共用的一张脸。
    const noAvatar = rowOf('wangchangxin')
    expect(noAvatar?.querySelector('img')).toBeNull()
    expect(noAvatar?.querySelector('.user-avatar-char')?.textContent).toBe('W')
  })

  it('根那组里平台上没有账号的人照常画一行：不报错、不空白', async () => {
    const { container, findByText } = mountPage()

    // 昵称、头像都为 null（平台上根本没这个账号）：主行退回 handle，这一行照常出现。
    const row = (await findByText('wangchangxin')).closest('tr')
    expect(row?.textContent).toContain('wangchangxin')
    // 头像那格是彩色首字母，不是空白。
    expect(row?.querySelector('.user-avatar-char')?.textContent).toBe('W')
    // 「平台里没这个账号」不是错误：加载完不该弹任何失败提示（`v-alert` 那一块）。
    expect(container.querySelector('.am__alert')).toBeNull()
  })

  it('账号状态与注册时间：没账号的明画、正常行画 —；agent 行挂徽章', async () => {
    const { container, findByText } = mountPage()
    await findByText('安迪')

    const rowOf = (needle: string) =>
      Array.from(container.querySelectorAll('tr.am__row')).find((r) => r.textContent?.includes(needle))
    // 六列：管理员 / 账号状态 / 注册时间 / 来源 / 添加信息 / 操作。
    const cellsOf = (needle: string) => rowOf(needle)?.querySelectorAll('td')

    // 平台上没有账号的行：状态格明画那句话（带 title 解释），注册时间格是 `—`
    // （没读到画 `—`，不画 0、也不画一个假时间）。
    const ghostCells = cellsOf('wangchangxin')
    expect(ghostCells?.[1].textContent).toContain('平台上没有这个账号')
    expect(ghostCells?.[1].querySelector('.am__warn')?.getAttribute('title')).toBeTruthy()
    expect(ghostCells?.[2].textContent?.trim()).toBe('—')

    // 正常行的状态格画 `—`：整页只有那一句警告 —— 不把整列刷成一片「正常」的噪音。
    expect(container.querySelectorAll('.am__warn')).toHaveLength(1)
    expect(cellsOf('安迪')?.[1].textContent?.trim()).toBe('—')
    expect(cellsOf('彭文博')?.[1].textContent?.trim()).toBe('—')

    // agent 徽章：`is_agent` 的根行，who 列（第一格）里挂 chip-neutral，带 title
    // 解释「为什么这行权限用不上」。全表就这一枚 —— 画到正常行身上也是错。
    const chip = cellsOf('cheese-bot')?.[0].querySelector('.chip-neutral')
    expect(chip?.textContent?.trim()).toBe('agent')
    expect(chip?.getAttribute('title')).toBeTruthy()
    expect(container.querySelectorAll('tr.am__row .chip-neutral')).toHaveLength(1)

    // 注册时间经 relTime 渲染 —— 同口径比对（import 同一个函数），不猜相对时间词。
    expect(cellsOf('安迪')?.[2].textContent).toContain(relTime(ANDY_REGISTERED))
    expect(cellsOf('彭文博')?.[2].textContent).toContain(relTime(PENG_REGISTERED))
  })

  it('已有名单时刷新：旧名单压暗（busy），不换骨架', async () => {
    const { container, findByText, getByRole } = mountPage()
    await findByText('安迪')
    expect(container.querySelector('.agrid--busy')).toBeNull()

    // 让下一次取数挂起：busy 本来只有半秒，拉长到断言跑完。
    let release: (value: PlatformAdminsPayload) => void = () => {}
    listPlatformAdmins.mockImplementation(
      () => new Promise<PlatformAdminsPayload>((resolve) => (release = resolve))
    )
    await fireEvent.click(getByRole('button', { name: '刷新' }))

    // 取数中：旧名单压暗、**内容还在**、没有换成骨架 —— 换骨架的话「安迪」会消失、
    // `.agrid__bone` 会出现。
    await vi.waitFor(() => expect(container.querySelector('.agrid--busy')).not.toBeNull())
    expect(container.querySelector('.agrid__bone')).toBeNull()
    expect(container.textContent).toContain('安迪')

    release(ROSTER)
    await vi.waitFor(() => expect(container.querySelector('.agrid--busy')).toBeNull())
  })

  it('确认框关掉之后，焦点回到触发它的那颗「移出」', async () => {
    const { findByText, findAllByRole } = mountPage()

    const removeButton = await findByText('移出')
    await fireEvent.click(removeButton)

    // 取消（浮层里那个），不是行上那颗。
    const cancels = await findAllByRole('button', { name: '取消' })
    const dialogCancel = cancels.find((b) => b.closest('.v-overlay'))
    expect(dialogCancel).toBeTruthy()
    await fireEvent.click(dialogCancel as HTMLElement)

    // Vuetify 的 `VDialog` 在没有 activator 时不会把焦点还回去，页面自己送。
    // `findByText` 给的是按钮里那层文字 `<span>`，焦点落在它外面那颗真 `<button>` 上。
    await vi.waitFor(() => expect(document.activeElement).toBe(removeButton.closest('button')))
  })

  it('取不到的头像：本次会话里不再发第二次请求', async () => {
    // 用一个没在别处出现过的 id：失败记忆是**模块级**的 Set，随这一份 spec 的进程
    // 生灭，复用别的用例的 id 会把它们的头像也短路成首字母。
    listPlatformAdmins.mockResolvedValue({
      root: [
        {
          handle: 'ghost',
          nickname: null,
          avatar_id: 424242,
          has_account: true,
          registered_at: ANDY_REGISTERED,
          is_agent: false,
        },
      ],
      added: [],
    })

    const first = mountPage()
    await first.findByText('ghost')
    const img = first.container.querySelector('tr.am__row img')
    expect(img, '第一次进来还是要去问一次，不能预判').not.toBeNull()
    img?.dispatchEvent(new Event('error'))
    await nextTick()
    first.unmount()

    // 再进一次：这个 URL 已知取不到（`utils/avatarFailures`），不该再造 `<img>` ——
    // 也就不会再发那一次必然 404 的请求，直接画彩色首字母。
    const second = mountPage()
    await second.findByText('ghost')
    expect(second.container.querySelector('tr.am__row img')).toBeNull()
    expect(second.container.querySelector('tr.am__row .user-avatar-char')?.textContent).toBe('G')
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
    // 这两条同时钉住 `<i18n-t>`（全仓首用）在 happy-dom 下把插槽正常渲出来。
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
