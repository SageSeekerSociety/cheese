/**
 * 队列页（`/admin/queue`，§4.1）。
 *
 * **这一组不 mock `@/api`，也不 mock store**：假数据接在 `window.fetch` 上（预览那套
 * fixture，`proto-feedback-fixtures.ts`），于是「api → store → 页 → 行组件」整条链子
 * 都真跑。手写一份 store 替身的话，断的就是「我调了我自己」—— 而这一页最贵的东西恰好
 * 是中间那几层：服务端栏位、页码、counts 的来路。
 *
 * 钉住三件只在整条链子上才成立的事：
 *
 * 1. 服务端那一页能一路走到行上（不是「渲染没抛错」）。
 * 2. 切视图、切状态页签**一个请求都不发**（§13 C-10：两个视图读同一份 `adminItems`）。
 *    这条是承诺，所以要有东西替它作证 —— 事后读代码看不出「确实没发」。
 * 3. 列表拉不到时画的是「队列加载失败 + 重试」，而**重试真的重拉**：第一次打 500、
 *    第二次放过去，于是「救得回来」是可验证的，不只是「按钮在」。
 */
import type { Component } from 'vue'

import { nextTick } from 'vue'
import { createRouter, createWebHashHistory } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterAll, beforeAll, beforeEach, describe, expect, it } from 'vitest'

import AdminQueuePage from './AdminQueuePage.vue'

import i18n, { setLocale } from '@/i18n'
import { installPreviewFetch } from '@/proto-feedback-fixtures'

/** 管理端的列表路由。详情是 `/api/admin/feedback/{id}`，所以这里按**整段相等**匹配。 */
const LIST_PATH = '/api/admin/feedback'

/** 推未读游标那一条（`POST /feedback/read`）。**它是「人按下的那一下」唯一的物证**：
 *  那个动作不改屏幕上任何东西（徽标本来就已经归零），只看页面是一点反应都没有。 */
const READ_PATH = '/api/feedback/read'

/** 假数据那层 fetch。**存成常量**：每次测试又包一层的话，包装器读的是个会变的变量，
 *  第二层就会调到自己，撞成栈溢出。 */
let preview: typeof window.fetch
let listCalls = 0
let readCalls = 0
/** 头几次列表请求打 500，之后放行 —— 于是「重试能救回来」是测得到的，不只是「按钮在」。 */
let failTimes = 0

beforeAll(() => {
  installPreviewFetch()
  preview = window.fetch
  // 面板上的状态名、四态那几句都是中文，而 `navigator.language` 在 happy-dom 里是
  // `en-US` —— 不钉住语言，断的就成了英文词条。
  setLocale('zh-CN')
})

beforeEach(() => {
  listCalls = 0
  readCalls = 0
  failTimes = 0
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const raw = typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url
    const url = new URL(raw, window.location.origin)
    if (url.pathname === READ_PATH) readCalls += 1
    if (url.pathname === LIST_PATH && (init?.method ?? 'GET').toUpperCase() === 'GET') {
      listCalls += 1
      if (failTimes > 0) {
        failTimes -= 1
        return new Response(JSON.stringify({ code: 500, message: '后端炸了', data: null }), {
          status: 500,
          headers: { 'content-type': 'application/json' },
        })
      }
    }
    return preview(input as RequestInfo, init)
  }
})

/** 套一层 `v-app`：页里那个抽屉是 `VNavigationDrawer`，它要一个 layout 才挂得上，
 *  而 layout 由 `v-app` 提供（`views/admin/AdminLayout.spec.ts` 也是这样挂的）。 */
const Wrapper = { components: { AdminQueuePage }, template: '<v-app><AdminQueuePage /></v-app>' }

async function mountQueue() {
  const router = createRouter({
    history: createWebHashHistory(),
    routes: [{ path: '/admin/queue', component: Wrapper }],
  })
  await router.push('/admin/queue')
  await router.isReady()
  const vuetify = createVuetify({ components, directives })
  return render(Wrapper as unknown as Component, { global: { plugins: [vuetify, createPinia(), router, i18n] } })
}

const rows = (container: Element) => container.querySelectorAll('.qrow')

describe('队列页', () => {
  it('把服务端那一页渲染成队列行，页头和工具行都在', async () => {
    const { container, findByText } = await mountQueue()

    expect(await findByText('反馈队列')).toBeTruthy()
    expect(await findByText('全部')).toBeTruthy()
    await waitFor(() => expect(rows(container).length).toBeGreaterThan(0))
    expect(listCalls).toBeGreaterThan(0)
  })

  it('切视图、切状态页签都不再取数（C-10）', async () => {
    const { container, getByText, getByRole } = await mountQueue()
    await waitFor(() => expect(rows(container).length).toBeGreaterThan(0))
    const before = listCalls

    await fireEvent.click(getByText('表格'))
    // 换的是同一个数组的另一种画法：总表挂上、队列行全下。
    expect(container.querySelector('.aft')).toBeTruthy()
    await waitFor(() => expect(rows(container).length).toBe(0))
    expect(listCalls).toBe(before)

    // 状态页签也是本地筛（服务端没有 status 这个参数），同样不该发请求。按 role 点而不是
    // 按字点：「已收录」这四个字在行上的状态芯片里也有一份。
    await fireEvent.click(getByRole('radio', { name: '已收录' }))
    expect(listCalls).toBe(before)
  })

  it('栏位控件：四个都画出来，换了就重新取数', async () => {
    const { container, getByRole } = await mountQueue()
    await waitFor(() => expect(rows(container).length).toBeGreaterThan(0))

    // 四个**都**画出来，不是一个下拉：这一页最贵的一类错是「管理员以为某条反馈不见了，
    // 其实它在隔壁那一栏」（服务端那个 400 就是为此存在的），所以当前停在哪一栏必须
    // 一眼看得见。
    const lanes = Array.from(container.querySelectorAll('.qpage__lane')).map((el) => el.textContent?.trim())
    expect(lanes).toEqual(['公开', '私密', 'AI 队友提的', '安全'])
    expect(getByRole('radio', { name: '公开' }).getAttribute('aria-checked')).toBe('true')

    // 换栏位是**服务端的问法**（`GET /admin/feedback?tab=`），所以必须重新取数 ——
    // 和上面那条「切状态页签一个请求都不发」正好相反，两者不是一回事。
    const before = listCalls
    await fireEvent.click(getByRole('radio', { name: '私密' }))

    await waitFor(() => expect(listCalls).toBeGreaterThan(before))
    expect(getByRole('radio', { name: '私密' }).getAttribute('aria-checked')).toBe('true')
    expect(getByRole('radio', { name: '公开' }).getAttribute('aria-checked')).toBe('false')
  })

  it('拉不到列表时画错误态，重试把它救回来', async () => {
    failTimes = 1

    const { container, findByText, getByText, queryByText } = await mountQueue()

    expect(await findByText('队列加载失败')).toBeTruthy()
    expect(rows(container).length).toBe(0)
    // 「一条都没有」和「没拉到」在这里必须长得不一样 —— 前者会让人以为平台坏了。
    expect(queryByText('暂无反馈')).toBeNull()

    const before = listCalls
    await fireEvent.click(getByText('重试'))

    await waitFor(() => expect(rows(container).length).toBeGreaterThan(0))
    expect(listCalls).toBeGreaterThan(before)
  })

  it('筛到零条说「筛掉了」，清除筛选把人捞回来', async () => {
    const { container, findByText, getByText, getByPlaceholderText } = await mountQueue()
    await waitFor(() => expect(rows(container).length).toBeGreaterThan(0))

    await fireEvent.update(getByPlaceholderText('搜索反馈'), 'zzz-没有这条')

    expect(await findByText('没有符合条件的反馈')).toBeTruthy()
    expect(container.querySelectorAll('.qrow').length).toBe(0)

    await fireEvent.click(getByText('清除筛选'))
    await waitFor(() => expect(rows(container).length).toBeGreaterThan(0))
  })
})

/** 窄屏那一档：`isWide` 由 `(min-width: 1280px)` 决定，而 happy-dom 里那个查询的答案
 *  不由视口宽度决定（没有布局）。所以这里**替它答**，而不是去改视口 —— 直接拨前者的
 *  话测的是「媒体查询返回 true 时怎么走」，不是「窄屏怎么走」。 */
describe('窄屏 + 抽屉开着（F-06 / F-13）', () => {
  const originalMatchMedia = window.matchMedia

  beforeAll(() => {
    window.matchMedia = ((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    })) as unknown as typeof window.matchMedia
  })

  afterAll(() => {
    window.matchMedia = originalMatchMedia
  })

  /** 总表那 11 列的行。**只取真行**（`aria-selected` 只有它们有，骨架和空态那两行
   *  没有），顺序就是服务端那一页的顺序。 */
  const tableRows = (container: Element) =>
    Array.from(container.querySelectorAll<HTMLElement>('.aft__row[aria-selected]'))
  const activeAt = (container: Element) =>
    tableRows(container).findIndex((tr) => tr.getAttribute('aria-selected') === 'true')

  /** 进总表、把第一行的链接当作焦点所在，然后按 `Enter` 把抽屉打开。
   *
   *  用 `Enter` 而不是点一下：点击那条路要先有一个 `pointerdown` 才被当成「点开」
   *  （`POINTER_WINDOW_MS` 那个时间窗），而时间窗在测试里是一颗会炸的雷 —— 它量的是
   *  真实时钟。`Enter` 是「打开」唯一的另一条路（两个列表都只在它上面发 `open`）。 */
  async function openDrawerInTable(container: Element, getByText: (s: string) => HTMLElement) {
    await fireEvent.click(getByText('表格'))
    await waitFor(() => expect(container.querySelector('.aft')).toBeTruthy())
    const first = tableRows(container)[0]?.querySelector<HTMLElement>('.fbrow__link')
    expect(first, '总表一行都没有，这条用例等于没做').toBeTruthy()
    first!.focus()
    expect(document.activeElement, '焦点没落在行上，守卫就测不到').toBe(first)
    await fireEvent.keyDown(first!, { key: 'Enter' })
    await waitFor(() => expect(container.querySelector('.v-navigation-drawer')).toBeTruthy())
    return first!
  }

  it('总表里按一次 j 只前进一行：格子已经收下的方向键，页面这一层不再走一遍', async () => {
    const { container, getByText } = await mountQueue()
    await waitFor(() => expect(rows(container).length).toBeGreaterThan(0))

    await openDrawerInTable(container, getByText)
    expect(activeAt(container)).toBe(0)

    // 焦点还在表格里 —— 抽屉开着、格子也还在 DOM 里（窄屏那两栏共用 `v-else`），
    // 所以两处都会收到这一颗键。以前守卫只认队列列表的类名（`.qlist`），总表整个漏在
    // 外面：一次 `j` 前进两行，光标**越过一行**，而那一行从此不会被任何人看见。
    const link = tableRows(container)[1 - 1]!.querySelector<HTMLElement>('.fbrow__link')!
    link.focus()
    await fireEvent.keyDown(link, { key: 'j' })

    await waitFor(() => expect(activeAt(container)).not.toBe(0))
    // 再等一拍：走两步的话第二拍才落下来，只等第一种变化会漏掉它。
    await nextTick()
    expect(activeAt(container), '一次 j 越过了一行').toBe(1)
  })

  it('M 每次真发一次请求（F-13）：`markReadOnce` 那套去重不该管人按下的这一颗', async () => {
    const { container } = await mountQueue()
    await waitFor(() => expect(rows(container).length).toBeGreaterThan(0))

    // 不点任何东西：**光标只是路过第一行**，那一次自动已读是 `markRead: false` 的，
    // 所以这里读到的两个数都是人按出来的。
    expect(readCalls).toBe(0)
    await fireEvent.keyDown(window, { key: 'M' })
    await waitFor(() => expect(readCalls).toBe(1))

    // 第二颗同样要发。以前这里调的是「同一条只推一次」那一版，于是第二颗被去重集合
    // 早退成静默空操作：未读数不减、徽标不动、也没有任何提示。
    await fireEvent.keyDown(window, { key: 'M' })
    await waitFor(() => expect(readCalls).toBe(2))
  })
})
