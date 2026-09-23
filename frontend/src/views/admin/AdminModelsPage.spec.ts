/**
 * 模型管理（`/admin/models`）：四态分开、无价不上架、危险动作先确认、失败照原话。
 *
 * 这一页管的是**网关那一侧的台账**，每一条都会当场改变全平台的路由（删一个模型、
 * 停用一个模型，项目立刻选不到），所以测试钉的是那些「静默坏掉、界面看着却没事」的
 * 地方：
 *
 * 1. **四态不许混**。loading / empty / error / ok 各自是一个不同的画法：拿不到数据
 *    画成一张空表，「接口挂了」就变成了「还没有模型」。error 那条宁可直接断言服务端
 *    原话（`boom` 这种）—— 页面答应过原样显示，改写一句「加载失败」就是把原因丢了。
 * 2. **上架开关没价时灰掉**。这是契约 §1 那条不变式的界面侧：无价模型会让项目的
 *    max_budget 这道刹车静默失效，所以界面在**提交之前**就把它拦住，而不是等服务端 400。
 * 3. **删除先确认再发请求**。删一个模型当场把它从选择器里摘掉，按钮又挨着一个名字，
 *    点错没有回头路。
 * 4. **写失败显示服务端原话**。一句「操作失败」会把这一页最需要的东西——为什么失败——
 *    丢掉；表单失败框还必须留着（关掉框等于把刚填的一屏字和原因一起丢）。
 *
 * 模子照 `AdminSpacesPage.spec.ts`：mock `@/api`、让 vue-i18n 的键透传（断言直接写键名）、
 * `createVuetify`、stub `ResizeObserver` / `visualViewport`（Vuetify 的浮层定位要读后者，
 * happy-dom 里没有，不补壳对话框一打开就抛）。
 */
import type { Component } from 'vue'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const getGatewayModels = vi.fn()
const getGatewayModel = vi.fn()
const createGatewayModel = vi.fn()
const updateGatewayModel = vi.fn()
const deleteGatewayModel = vi.fn()
const setGatewayModelBlocked = vi.fn()
const getGatewayProjects = vi.fn()
const setGatewayProjectBudget = vi.fn()
const getGatewayAudit = vi.fn()
// 订阅族：页面（导入对话框）与详情抽屉（额度/移除/重新授权）用到的七个函数。
// 漏掉一个，组件就会去打真网络，被 `setup-network.ts` 判红。
const startSubscriptionDeviceFlow = vi.fn()
const pollSubscriptionDeviceFlow = vi.fn()
const cancelSubscriptionDeviceFlow = vi.fn()
const listSubscriptions = vi.fn()
const refreshSubscription = vi.fn()
const getSubscriptionQuota = vi.fn()
const revokeSubscription = vi.fn()

// 整块换掉 `@/api`，**不走 `importActual`**：真模块会一路 import 到 i18n 的 barrel，
// 而那一层要 `createI18n`，与本文件对 vue-i18n 的透传 mock 直接冲突（收集阶段就崩）。
// 页面和详情抽屉一共用这十六个函数，列全即可；漏掉一个它会去打真网络，被
// `setup-network.ts` 判红。
vi.mock('@/api', () => ({
  getGatewayModels: (...a: unknown[]) => getGatewayModels(...a),
  getGatewayModel: (...a: unknown[]) => getGatewayModel(...a),
  createGatewayModel: (...a: unknown[]) => createGatewayModel(...a),
  updateGatewayModel: (...a: unknown[]) => updateGatewayModel(...a),
  deleteGatewayModel: (...a: unknown[]) => deleteGatewayModel(...a),
  setGatewayModelBlocked: (...a: unknown[]) => setGatewayModelBlocked(...a),
  getGatewayProjects: (...a: unknown[]) => getGatewayProjects(...a),
  setGatewayProjectBudget: (...a: unknown[]) => setGatewayProjectBudget(...a),
  getGatewayAudit: (...a: unknown[]) => getGatewayAudit(...a),
  startSubscriptionDeviceFlow: (...a: unknown[]) => startSubscriptionDeviceFlow(...a),
  pollSubscriptionDeviceFlow: (...a: unknown[]) => pollSubscriptionDeviceFlow(...a),
  cancelSubscriptionDeviceFlow: (...a: unknown[]) => cancelSubscriptionDeviceFlow(...a),
  listSubscriptions: (...a: unknown[]) => listSubscriptions(...a),
  refreshSubscription: (...a: unknown[]) => refreshSubscription(...a),
  getSubscriptionQuota: (...a: unknown[]) => getSubscriptionQuota(...a),
  revokeSubscription: (...a: unknown[]) => revokeSubscription(...a),
}))
vi.mock('vue-i18n', () => ({ useI18n: () => ({ t: (key: string) => key }) }))

import AdminModelsPage from './AdminModelsPage.vue'

/** 一个用量块，字段齐但值随意；要改哪一项就传进来。 */
function usage(over: Record<string, number> = {}) {
  return {
    spend_usd: 1,
    requests: 10,
    failed_requests: 0,
    prompt_tokens: 0,
    completion_tokens: 0,
    cache_read_tokens: 0,
    total_tokens: 1000,
    ...over,
  }
}

/** 一个**运行时**模型：可改可删可停用，所以它在页面上有那三个按钮。 */
function runtimeModel(over: Record<string, unknown> = {}) {
  return {
    name: 'glm-4.7-runtime',
    model_id: 'rt-1',
    label: 'GLM 4.7',
    origin: 'runtime',
    blocked: false,
    selectable: true,
    priced: true,
    offered: true,
    blocked_reason: null,
    unpriced_reason: null,
    upstream: { model: 'anthropic/glm-4.7', host: 'open.bigmodel.cn', provider: 'anthropic' },
    prices: { input: 1e-6, output: 2e-6 },
    capabilities: { reasoning: true },
    usage: usage(),
    series: [10, 20, 15, 30, 25, 40, 35],
    subscription: null,
    ...over,
  }
}

/** 一条由订阅喂养的运行时模型：来源徽章是「订阅」，单价下多一句估计值说明。 */
function subscriptionModel(over: Record<string, unknown> = {}) {
  return runtimeModel({
    name: 'gpt-codex-subscription',
    model_id: 'rt-sub',
    label: 'GPT · ChatGPT 订阅',
    upstream: { model: 'openai/gpt-5.2-codex', host: 'chatgpt.com', provider: 'openai' },
    subscription: {
      id: 'sub-1',
      status: 'active',
      account_email: 'admin@example.com',
      quota: null,
    },
    ...over,
  })
}

/** config 来源的模型：只读。它存在，是为了钉住「它那一行没有编辑/删除/停用按钮」。 */
function configModel(over: Record<string, unknown> = {}) {
  return {
    name: 'mimo-v2.6-pro',
    model_id: 'cfg-1',
    label: 'MiMo V2.6 Pro',
    origin: 'config',
    blocked: false,
    selectable: true,
    priced: true,
    offered: true,
    blocked_reason: null,
    unpriced_reason: null,
    upstream: { model: 'anthropic/mimo-v2.6-pro', host: 'token-plan-cn.xiaomimimo.com', provider: 'anthropic' },
    prices: { input: 4.2e-7, output: 8.4e-7 },
    capabilities: { reasoning: true, vision: true },
    usage: usage(),
    series: [1, 2, 3, 4, 5, 6, 7],
    subscription: null,
    ...over,
  }
}

function modelsPayload(models: unknown[]) {
  return {
    gateway: {
      reachable: true,
      readiness: 'healthy',
      admin_configured: true,
      detail: null,
      fetched_at: '2026-09-23T02:40:00+00:00',
    },
    window: { days: 7, start_date: '2026-09-16', end_date: '2026-09-23' },
    totals: usage(),
    models,
  }
}

function projectsPayload(projects: unknown[] = []) {
  return {
    window: { days: 7, start_date: '2026-09-16', end_date: '2026-09-23' },
    projects,
    totals: { projects: projects.length, with_key: projects.length, over_budget: 0, unlimited: 0 },
  }
}

// 包一层 `<v-app>`：详情抽屉是 `VNavigationDrawer`，它要读 Vuetify 注入的 layout，
// 裸挂会让 `<v-drawer>` 的 setup 直接抛「Could not find injected layout」。
function mountPage() {
  const vuetify = createVuetify({ components, directives })
  const Wrapper = { components: { AdminModelsPage }, template: '<v-app><AdminModelsPage /></v-app>' }
  return render(Wrapper as unknown as Component, { global: { plugins: [vuetify] } })
}

beforeAll(() => {
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  )
  vi.stubGlobal('visualViewport', {
    width: 1024,
    height: 768,
    offsetLeft: 0,
    offsetTop: 0,
    pageLeft: 0,
    pageTop: 0,
    scale: 1,
    addEventListener() {},
    removeEventListener() {},
  })
})

beforeEach(() => {
  getGatewayModels.mockReset().mockResolvedValue(modelsPayload([runtimeModel()]))
  getGatewayModel.mockReset().mockResolvedValue({})
  createGatewayModel.mockReset().mockResolvedValue({})
  updateGatewayModel.mockReset().mockResolvedValue({})
  deleteGatewayModel.mockReset().mockResolvedValue({ deleted: true })
  setGatewayModelBlocked.mockReset().mockResolvedValue({ blocked: true })
  getGatewayProjects.mockReset().mockResolvedValue(projectsPayload())
  setGatewayProjectBudget.mockReset().mockResolvedValue({})
  getGatewayAudit.mockReset().mockResolvedValue({ items: [] })
  startSubscriptionDeviceFlow.mockReset()
  pollSubscriptionDeviceFlow.mockReset()
  cancelSubscriptionDeviceFlow.mockReset()
  listSubscriptions.mockReset().mockResolvedValue({ items: [] })
  refreshSubscription.mockReset()
  getSubscriptionQuota.mockReset()
  revokeSubscription.mockReset()
})
afterEach(cleanup)

describe('模型管理 · 四态', () => {
  it('数据还没到货时画骨架，不画空表', async () => {
    // 永不 resolve：首屏状态停在那儿，测的就是那一刻的画法。
    getGatewayModels.mockReturnValue(new Promise(() => {}))
    const page = mountPage()

    const grid = page.getByRole('table', { name: 'models.table.label' })
    await waitFor(() => expect(grid.getAttribute('aria-busy')).toBe('true'))
    // 骨架行是真的 `<tr>`（表壳的取舍），所以此时既没有空态那句话，也没有数据行。
    expect(page.queryByText('models.table.empty')).toBeNull()
    expect(page.queryByText('GLM 4.7')).toBeNull()
  })

  it('有模型时把行画出来', async () => {
    const page = mountPage()
    // label 优先于 name 显示：网关里的人给了 label 就听他的。
    expect(await page.findByText('GLM 4.7')).toBeTruthy()
    expect(page.getByRole('table', { name: 'models.table.label' }).getAttribute('aria-busy')).toBe('false')
  })

  it('一条模型都没有时画空态，而不是一直骨架', async () => {
    getGatewayModels.mockResolvedValue(modelsPayload([]))
    const page = mountPage()
    expect(await page.findByText('models.table.empty')).toBeTruthy()
  })

  it('加载失败时原样显示服务端原因，不画成「还没有模型」', async () => {
    getGatewayModels.mockRejectedValue(new Error('网关不可达：connection refused'))
    const page = mountPage()

    expect(await page.findByText(/connection refused/)).toBeTruthy()
    // 空态和错误是两件事：拿不到数据时绝不能同时出现「暂无模型」。
    expect(page.queryByText('models.table.empty')).toBeNull()
  })
})

describe('模型管理 · 上架闸门', () => {
  it('没价时上架开关是禁用的，两档价都填了才放开', async () => {
    const page = mountPage()
    await page.findByText('GLM 4.7')

    await fireEvent.click(page.getByRole('button', { name: 'models.page.add' }))
    await page.findByText('models.dialog.add.title')

    const sw = () => page.getByLabelText('models.dialog.field.selectable') as HTMLInputElement
    // 空表单：input/output 都没价 ⇒ 开关灰掉（契约 §1 的不变式在界面侧）。
    expect(sw().disabled).toBe(true)

    await fireEvent.update(page.getByLabelText('models.price.input'), '1')
    await fireEvent.update(page.getByLabelText('models.price.output'), '2')
    await waitFor(() => expect(sw().disabled).toBe(false))
  })
})

describe('模型管理 · 危险动作', () => {
  it('删除先确认，确认之后才发请求', async () => {
    const page = mountPage()
    await page.findByText('GLM 4.7')

    await fireEvent.click(page.getByRole('button', { name: 'models.table.action.delete' }))
    // 弹确认框之前一个请求都不该出去。
    expect(deleteGatewayModel).not.toHaveBeenCalled()
    expect(await page.findByText('models.confirm.delete.title')).toBeTruthy()

    await fireEvent.click(page.getByRole('button', { name: 'models.confirm.delete.confirm' }))
    await waitFor(() => expect(deleteGatewayModel).toHaveBeenCalledWith('glm-4.7-runtime'))
  })

  it('config 来源的模型那一行只读：不给编辑/删除/停用按钮，给「查看改法」入口', async () => {
    getGatewayModels.mockResolvedValue(modelsPayload([configModel()]))
    const page = mountPage()
    await page.findByText('MiMo V2.6 Pro')

    expect(page.queryByRole('button', { name: 'models.table.action.edit' })).toBeNull()
    expect(page.queryByRole('button', { name: 'models.table.action.delete' })).toBeNull()
    // 一句死「只读」是信息的终点；「查看改法」按钮点开抽屉（里面有 config 复制卡）。
    const howToEdit = page.getByRole('button', { name: 'models.table.howToEdit' })
    await fireEvent.click(howToEdit)
    await waitFor(() => expect(getGatewayModel).toHaveBeenCalledWith('mimo-v2.6-pro', 7))
  })
})

describe('模型管理 · 写失败', () => {
  it('删除失败时把服务端原话显示在页面上', async () => {
    deleteGatewayModel.mockRejectedValue(new Error('网关拒绝：模型在 config 里，请改 config.yaml'))
    const page = mountPage()
    await page.findByText('GLM 4.7')

    await fireEvent.click(page.getByRole('button', { name: 'models.table.action.delete' }))
    await page.findByText('models.confirm.delete.title')
    await fireEvent.click(page.getByRole('button', { name: 'models.confirm.delete.confirm' }))

    expect(await page.findByText(/请改 config\.yaml/)).toBeTruthy()
  })

  it('表单提交失败时把原因留在框里，且框不关', async () => {
    createGatewayModel.mockRejectedValue(new Error('无价模型不能上架：缺输出单价'))
    const page = mountPage()
    await page.findByText('GLM 4.7')

    await fireEvent.click(page.getByRole('button', { name: 'models.page.add' }))
    await page.findByText('models.dialog.add.title')
    await fireEvent.update(page.getByLabelText('models.dialog.field.name'), 'glm-4.7-x')
    await fireEvent.update(page.getByLabelText('models.dialog.field.upstreamModel'), 'anthropic/glm-4.7')
    await fireEvent.click(page.getByRole('button', { name: 'models.dialog.create' }))

    expect(await page.findByText(/缺输出单价/)).toBeTruthy()
    // 框还开着：关掉框等于把刚填的一屏字和「为什么退回」一起丢掉。
    expect(page.getByText('models.dialog.add.title')).toBeTruthy()
  })
})

describe('模型管理 · 重设计后的列表', () => {
  it('订阅模型：来源徽章是「订阅」、状态列有订阅状态、单价下有估计值说明', async () => {
    getGatewayModels.mockResolvedValue(modelsPayload([subscriptionModel()]))
    const page = mountPage()
    await page.findByText('GPT · ChatGPT 订阅')

    // 三种来源徽章各是各的词：这一行不是「运行时新增」。
    expect(page.getByText('models.table.origin.subscription')).toBeTruthy()
    expect(page.queryByText('models.table.origin.runtime')).toBeNull()
    // 状态列第二行是订阅状态（active → 订阅正常）。
    expect(page.getByText('models.table.subscriptionOk')).toBeTruthy()
    // 包月订阅的花费是估计值，这句话必须在单价旁边。
    expect(page.getByText('models.table.estimateNote')).toBeTruthy()
  })

  it('订阅状态跟着订阅走：refresh_failed 与 reauth_required 各是它的那句', async () => {
    getGatewayModels.mockResolvedValue(
      modelsPayload([
        subscriptionModel({
          name: 'sub-a',
          label: 'A',
          subscription: { id: 's1', status: 'refresh_failed', account_email: null, quota: null },
        }),
        subscriptionModel({
          name: 'sub-b',
          label: 'B',
          subscription: { id: 's2', status: 'reauth_required', account_email: null, quota: null },
        }),
      ])
    )
    const page = mountPage()
    await page.findByText('A')
    expect(page.getByText('models.table.subscriptionRefreshFailed')).toBeTruthy()
    expect(page.getByText('models.table.subscriptionReauth')).toBeTruthy()
  })

  it('状态列画失败率：有请求给百分比，0 请求画 —', async () => {
    getGatewayModels.mockResolvedValue(
      modelsPayload([
        runtimeModel({ name: 'm-fail', label: 'Fail', usage: usage({ requests: 200, failed_requests: 20 }) }),
        runtimeModel({ name: 'm-idle', label: 'Idle', usage: usage({ requests: 0, failed_requests: 0 }) }),
      ])
    )
    const page = mountPage()
    await page.findByText('Fail')
    // 20/200 = 10%：fmtPercent 的口径（≥10% 取整）。
    expect(page.getByText('10%')).toBeTruthy()
    // 0 请求：画「—」，不是 0%（「没用到」不是「没失败」）。
    const idleRow = (await page.findByText('Idle')).closest('tr')!
    expect(idleRow.textContent).toContain('—')
    expect(idleRow.textContent).not.toContain('0%')
  })

  it('页头有 readiness 健康灯：readiness 文本与拉取时间在', async () => {
    const page = mountPage()
    await page.findByText('GLM 4.7')
    expect(page.getByText(/healthy/)).toBeTruthy()
  })

  it('「导入订阅」按钮打开导入对话框', async () => {
    const page = mountPage()
    await page.findByText('GLM 4.7')
    await fireEvent.click(page.getByRole('button', { name: 'models.subscription.import' }))
    expect(await page.findByText('models.subscription.dialogTitle')).toBeTruthy()
  })
})

describe('模型管理 · 审计区 diff', () => {
  it('「查看改动」展开字段级 diff：旧值 → 新值；没有快照的项不给按钮', async () => {
    getGatewayAudit.mockResolvedValue({
      items: [
        {
          created_at: '2026-09-23T02:40:00+00:00',
          actor_handle: 'admin',
          action: 'model.update',
          target: 'glm-4.7-runtime',
          result: 'ok',
          detail: null,
          before: { label: '旧标签', selectable: false },
          after: { label: '新标签', selectable: true },
        },
        {
          created_at: '2026-09-23T02:41:00+00:00',
          actor_handle: 'admin',
          action: 'model.delete',
          target: 'x',
          result: 'ok',
          detail: null,
          before: null,
          after: null,
        },
      ],
    })
    const page = mountPage()
    await page.findByText('GLM 4.7')

    // 两条审计只有一个「查看改动」按钮：before/after 都为空的那条没有可看的。
    const buttons = await page.findAllByRole('button', { name: 'models.audit.diff.show' })
    expect(buttons).toHaveLength(1)

    await fireEvent.click(buttons[0])
    // 字段名走字面量词条（不是实现键名），值是「旧 → 新」，布尔是「是 / 否」。
    expect(await page.findByText('models.audit.diff.field.label')).toBeTruthy()
    expect(page.getByText('旧标签')).toBeTruthy()
    expect(page.getByText('新标签')).toBeTruthy()
    expect(page.getByText('models.audit.diff.no')).toBeTruthy()
    expect(page.getByText('models.audit.diff.yes')).toBeTruthy()

    // 再点一下收起。
    await fireEvent.click(page.getByRole('button', { name: 'models.audit.diff.hide' }))
    expect(page.queryByText('models.audit.diff.field.label')).toBeNull()
  })

  it('订阅动作有词条：不再落成「其它操作」', async () => {
    getGatewayAudit.mockResolvedValue({
      items: [
        {
          created_at: '2026-09-23T02:40:00+00:00',
          actor_handle: 'admin',
          action: 'subscription.complete',
          target: 'sub-1',
          result: 'ok',
          detail: null,
          before: null,
          after: { status: 'active' },
        },
      ],
    })
    const page = mountPage()
    await page.findByText('GLM 4.7')
    expect(await page.findByText('models.audit.action.subscriptionComplete')).toBeTruthy()
  })
})
