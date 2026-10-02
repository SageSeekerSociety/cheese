/**
 * 模型管理（`/admin/models`）**三段各自画出了什么**。
 *
 * `AdminModelsPage.spec.ts` 管的是接线（四态、读不到网关、上架闸门、危险动作、写失败
 * 照原话）。这一份管的是「三段画出来的东西」—— 页头那六个数与窗口、模型那一张表的每
 * 一格、额度那一段的四种刹车值说法、审计那一段的一行、以及页顶那条提示。
 *
 * 它存在的直接原因是一次拆分：这一页 1428 行，要按 #2199（看板）那一份的形状拆成
 * 「取数在 composable、画在各段自己的组件里」。拆之前先把三段画出来的东西钉住 ——
 * 拆完这一份一个字都不用改，全绿才是「行为没变」。
 *
 * 钉的是一段里的**第一层读数与那句话**：KPI 的值、表里每一格的字、没读到的那一段说
 * 的是哪一句。不钉像素、不钉 DOM 层级（拆分会动层级，屏幕上的字不会）。
 *
 * 这里用**真的 i18n**（不是 `AdminModelsPage.spec.ts` 那份透传 mock）：被钉住的有一半
 * 是中文句子（「已停用」「按额度折为 $20.00」），透传键名会把它们全换成键名。
 */
import type { Component } from 'vue'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor, within } from '@testing-library/vue'
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

// 整块换掉 `@/api`：真模块会一路 import 到网络层，而这一份要跑的是页面本身。
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
}))

import AdminModelsPage from './AdminModelsPage.vue'

import i18n, { setLocale } from '@/i18n'

/** 一个用量块，字段齐但值随意；要改哪一项就传进来。 */
function usage(over: Record<string, number> = {}) {
  return { spend_usd: 1, requests: 10, failed_requests: 0, total_tokens: 1000, ...over }
}

/** 一行模型。默认是**运行时**那一档：三个动作按钮都在。 */
function model(over: Record<string, unknown> = {}) {
  return {
    name: 'glm-4.7-runtime',
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
    ...over,
  }
}

/** 一行项目。默认没有刹车值，但额度折得出来一个建议值。 */
function project(over: Record<string, unknown> = {}) {
  return {
    project_id: 'p-1',
    name: '知是平台后端',
    key_alias: 'zhishi',
    has_key: true,
    gateway_spend_usd: 12.5,
    max_budget_usd: null,
    budget_derived_usd: 20,
    budget_override_usd: null,
    credits: { total: 100, used: 30, remaining: 70, unlimited: false },
    usage: usage({ spend_usd: 12.5, requests: 30, total_tokens: 3000 }),
    ...over,
  }
}

function modelsPayload(models: unknown[], gateway: Record<string, unknown> = {}) {
  return {
    gateway: {
      reachable: true,
      readiness: 'healthy',
      admin_configured: true,
      detail: null,
      fetched_at: '2026-09-23T02:40:00+00:00',
      ...gateway,
    },
    window: { days: 7, start_date: '2026-09-16', end_date: '2026-09-23' },
    totals: usage(),
    models,
  }
}

function projectsPayload(projects: unknown[], totals: Record<string, unknown> = {}) {
  return {
    window: { days: 7, start_date: '2026-09-16', end_date: '2026-09-23' },
    projects,
    totals: { projects: projects.length, with_key: projects.length, over_budget: 0, unlimited: 0, ...totals },
  }
}

/** 一条审计：默认有前后两份快照，所以「查看改动」该出现。 */
function auditItem(over: Record<string, unknown> = {}) {
  return {
    created_at: '2026-09-23T02:40:00+00:00',
    actor_handle: 'admin',
    action: 'model.update',
    target: 'glm-4.7-runtime',
    result: 'ok',
    detail: null,
    before: { label: '旧标签' },
    after: { label: '新标签' },
    ...over,
  }
}

// 包一层 `<v-app>`：详情抽屉是 `VNavigationDrawer`，它要读 Vuetify 注入的 layout。
function mountPage() {
  const vuetify = createVuetify({ components, directives })
  const Wrapper = { components: { AdminModelsPage }, template: '<v-app><AdminModelsPage /></v-app>' }
  return render(Wrapper as unknown as Component, { global: { plugins: [vuetify, i18n] } })
}

/** 挂起来并等**模型与额度两段都到货**。
 *
 *  等的是内容而不是容器：这两段的壳（`<section>`、`.amd__gridwrap`）在数据到货之前
 *  就已经在树上了，只等壳的话拿到的是六行骨架、一张空表。 */
async function mountLoaded() {
  const page = mountPage()
  await page.findByText('GLM 4.7')
  await page.findByText('知是平台后端')
  return page
}

/** KPI 行上每一张卡的标题 / 值，从左到右。 */
function kpiLabels(container: Element): string[] {
  return Array.from(container.querySelectorAll('.amd__kpis .akpi__label')).map((el) => el.textContent?.trim() ?? '')
}

function kpiValues(container: Element): string[] {
  return Array.from(container.querySelectorAll('.amd__kpis .akpi__num')).map((el) => el.textContent?.trim() ?? '')
}

/** 选中的那一批元素各自的文字，空白收成一格。 */
function texts(container: Element, selector: string): string[] {
  return Array.from(container.querySelectorAll(selector)).map((el) => el.textContent?.replace(/\s+/g, ' ').trim() ?? '')
}

/** 第 n 张表（0 = 模型，1 = 额度）整块的文字。 */
function gridText(container: Element, index: number): string {
  return texts(container, '.amd__gridwrap')[index] ?? ''
}

beforeAll(() => {
  setLocale('zh-CN')
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
  getGatewayModels.mockReset().mockResolvedValue(modelsPayload([model()]))
  getGatewayModel.mockReset().mockResolvedValue({})
  createGatewayModel.mockReset().mockResolvedValue({})
  updateGatewayModel.mockReset().mockResolvedValue({})
  deleteGatewayModel.mockReset().mockResolvedValue({ deleted: true })
  setGatewayModelBlocked.mockReset().mockResolvedValue({ blocked: true })
  getGatewayProjects.mockReset().mockResolvedValue(projectsPayload([project()]))
  setGatewayProjectBudget.mockReset().mockResolvedValue({})
  getGatewayAudit.mockReset().mockResolvedValue({ items: [] })
})
afterEach(cleanup)

describe('模型管理 · 页头与六个 KPI', () => {
  it('标题、一句说明（带当前窗口）、三个档位与刷新', async () => {
    const page = await mountLoaded()

    expect(page.getByRole('heading', { name: '模型', level: 1 })).toBeTruthy()
    // 窗口是三段共用的，所以在页头只说一次；说的时候把它连起止日期一起说。
    expect(page.getByText('网关上的模型、单价与每个项目的额度 · 2026-09-16 – 2026-09-23')).toBeTruthy()

    const tabs = page.getByRole('tablist', { name: '时间窗口' })
    expect(
      within(tabs)
        .getAllByRole('tab')
        .map((b) => b.textContent?.trim())
    ).toEqual(['过去 7 天', '过去 14 天', '过去 30 天'])
    expect(within(tabs).getByRole('tab', { name: '过去 7 天' }).getAttribute('aria-selected')).toBe('true')

    await fireEvent.click(within(tabs).getByRole('tab', { name: '过去 14 天' }))
    await waitFor(() => expect(getGatewayModels).toHaveBeenLastCalledWith(14))
  })

  it('刷新按钮真重拉（不是把旧数据留在屏幕上）', async () => {
    const page = await mountLoaded()

    await fireEvent.click(page.getByRole('button', { name: '刷新' }))
    await waitFor(() => expect(getGatewayModels).toHaveBeenCalledTimes(2))
  })

  it('六个 KPI 是这一页的六个口径，格式各自对得上', async () => {
    const page = await mountLoaded()

    expect(kpiLabels(page.container)).toEqual([
      '模型',
      '已上架',
      '窗口内花费',
      '窗口内 token',
      '窗口内调用',
      '失败请求',
    ])
    // 1 行模型、1 行上架、$1.00 花费、1000 token 缩写成 1.0k（NBSP）、10 次调用、0 次失败。
    expect(kpiValues(page.container)).toEqual(['1', '1', '$1.00', '1.0 k', '10', '0'])
  })

  it('还没到货时六张卡是骨架的形状（六格），一个数都不画', async () => {
    getGatewayModels.mockReturnValue(new Promise(() => {}))
    const page = mountPage()

    await waitFor(() => expect(page.container.querySelectorAll('.amd__kpis .akpi').length).toBe(6))
    // 骨架是同样的六个格子（到货那一刻不重排），但没有一格是 `.akpi__num` ——
    // 破折号是「读到了、值是空」，那要等数据到了才算数。
    expect(page.container.querySelectorAll('.amd__kpis .akpi__num')).toHaveLength(0)
  })
})

describe('模型管理 · 模型那一段', () => {
  it('一行运行时模型：来源、两档单价、窗口用量、上架与失败率都在', async () => {
    const page = await mountLoaded()

    const row = texts(page.container, '.amd__gridwrap tbody tr')[0]!
    expect(row).toContain('GLM 4.7')
    // label 是主字，网关里的 name 是下面那行小字。
    expect(row).toContain('glm-4.7-runtime')
    expect(row).toContain('运行时新增')
    // 单价换算成「每百万 token 的美元」（1e-6 → $1.00）。
    expect(row).toContain('$1.00')
    expect(row).toContain('$2.00')
    // 窗口用量：花费、调用次数、token。
    expect(row).toContain('10 次调用')
    expect(row).toContain('1.0 k')
    expect(row).toContain('已上架')
    // 10 次请求 0 次失败 → 0%（失败率是一个例外状态，正常时不抢眼）。
    expect(row).toContain('0%')
  })

  it('被停用的模型：上架那一格写「已停用」，并把服务端给的原因挂在旁边', async () => {
    getGatewayModels.mockResolvedValue(
      modelsPayload([model({ blocked: true, offered: false, blocked_reason: '上游限流，先摘下来' })])
    )
    const page = await mountLoaded()

    const row = texts(page.container, '.amd__gridwrap tbody tr')[0]!
    expect(row).toContain('已停用')
    expect(row).toContain('上游限流，先摘下来')
  })

  it('config 来源的行只读：那一格是「只读 · 查看改法」，不是三个死按钮', async () => {
    getGatewayModels.mockResolvedValue(
      modelsPayload([model({ origin: 'config', name: 'mimo-v2.6-pro', label: 'MiMo' })])
    )
    const page = mountPage()
    await page.findByText('MiMo')

    const row = page.getByText('MiMo').closest('tr')!
    expect(row.textContent).toContain('配置文件')
    expect(within(row).getByRole('button', { name: '只读 · 查看改法' })).toBeTruthy()
    expect(within(row).queryByRole('button', { name: '编辑' })).toBeNull()
    expect(within(row).queryByRole('button', { name: '删除' })).toBeNull()
  })

  it('网关在但读不到：表里那条说明就是这件事，新增模型也灰掉', async () => {
    getGatewayModels.mockResolvedValue(modelsPayload([], { reachable: false, readiness: null }))
    const page = mountPage()

    expect(await page.findByText('读不到网关的模型清单。确认网关在运行，然后重试。')).toBeTruthy()
    // 网关读不出来时不给「新增模型」：那条路也要网关。
    expect((page.getByRole('button', { name: '新增模型' }) as HTMLButtonElement).disabled).toBe(true)
    expect(page.queryByText('暂无模型')).toBeNull()
  })

  it('这一次请求就失败了：表里是服务端原话（不是「暂无模型」），重试就在旁边', async () => {
    getGatewayModels.mockRejectedValue(new Error('boom'))
    const page = mountPage()

    const message = await page.findByText('boom')
    // 位置说明「这张表没读出来」，而不是页顶一条谁也不挨着谁的横幅。
    expect(message.closest('tbody')).not.toBeNull()
    expect(page.queryByText('暂无模型')).toBeNull()

    await fireEvent.click(page.getByRole('button', { name: '重试' }))
    await waitFor(() => expect(getGatewayModels).toHaveBeenCalledTimes(2))
  })

  it('网关在但没配管理密钥：说的是「没配密钥」那一句，读的人知道该去配什么', async () => {
    getGatewayModels.mockResolvedValue(modelsPayload([], { readiness: null, admin_configured: false }))
    const page = mountPage()

    expect(await page.findByText('这个部署没有配网关管理密钥，模型与额度都读写不了。')).toBeTruthy()
    // 健康灯说的是短句，全文挂在它的 title 上：两处写同一句话会让人以为是两次失败。
    const light = page.getByRole('status', { name: '网关状态' })
    expect(light.textContent).toContain('网关读不到')
    expect((page.getByRole('button', { name: '新增模型' }) as HTMLButtonElement).disabled).toBe(true)
    expect(page.queryByText('暂无模型')).toBeNull()
  })
})

describe('模型管理 · 额度那一段', () => {
  it('摘要一句话说清四个数，列是项目 / 花费 / 用量 / 额度 / 刹车值', async () => {
    const page = await mountLoaded()

    expect(page.getByText('1 个项目，1 个有密钥，0 个超出额度，0 个不限量')).toBeTruthy()
    expect(page.container.querySelectorAll('.amd__gridwrap')).toHaveLength(2)
    const head = gridText(page.container, 1)
    expect(head).toContain('窗口内花费')
    expect(head).toContain('刹车值')
  })

  it('没设刹车值：写「未设」，并给出按额度折出来的那个建议值', async () => {
    const page = await mountLoaded()

    const row = gridText(page.container, 1)
    expect(row).toContain('知是平台后端')
    expect(row).toContain('未设')
    expect(row).toContain('按额度折为 $20.00')
    // 额度那一格：剩余在前，已用 / 总数在后。
    expect(row).toContain('70')
    expect(row).toContain('已用 30 / 100')
  })

  it('有覆盖值：写它自己那个数，并标明这是一个覆盖', async () => {
    getGatewayProjects.mockResolvedValue(
      projectsPayload([project({ max_budget_usd: 50, budget_override_usd: 50, budget_derived_usd: 20 })])
    )
    const page = await mountLoaded()

    const row = gridText(page.container, 1)
    expect(row).toContain('$50.00')
    expect(row).toContain('有覆盖')
    expect(row).not.toContain('按额度折为')
  })

  it('不限量是一个结论，先说，不让人去比总数和已用', async () => {
    getGatewayProjects.mockResolvedValue(
      projectsPayload([project({ credits: { total: null, used: 0, remaining: 0, unlimited: true } })])
    )
    const page = await mountLoaded()

    const row = gridText(page.container, 1)
    expect(row).toContain('不限量')
    expect(row).not.toContain('已用')
  })

  it('没有密钥的项目不能设额度（设了也没处落）', async () => {
    getGatewayProjects.mockResolvedValue(projectsPayload([project({ key_alias: '', has_key: false })]))
    const page = await mountLoaded()

    expect((page.getByRole('button', { name: '设额度' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('读失败画在它自己的位置上（不退化成「暂无项目」），重试只重拉这一段', async () => {
    getGatewayProjects.mockRejectedValue(new Error('502 bad gateway'))
    const page = mountPage()
    await page.findByText('GLM 4.7')

    const message = await page.findByText('502 bad gateway')
    expect(message.closest('tbody')).not.toBeNull()
    expect(page.queryByText('暂无项目')).toBeNull()

    await fireEvent.click(page.getByRole('button', { name: '重试' }))
    await waitFor(() => expect(getGatewayProjects).toHaveBeenCalledTimes(2))
    // 这一段的重试不该顺手把模型表也重拉一遍。
    expect(getGatewayModels).toHaveBeenCalledTimes(1)
  })
})

describe('模型管理 · 最近操作那一段', () => {
  it('一行审计：动作说人话、目标是哪个、结果是什么', async () => {
    getGatewayAudit.mockResolvedValue({
      items: [
        auditItem({ detail: '改了单价' }),
        auditItem({ action: 'model.delete', result: 'failed', before: null, after: null }),
      ],
    })
    const page = await mountLoaded()

    await waitFor(() => expect(page.container.querySelectorAll('.amd__auditRow').length).toBe(2))
    const rows = texts(page.container, '.amd__auditRow')
    expect(rows[0]).toContain('admin')
    expect(rows[0]).toContain('改模型')
    expect(rows[0]).toContain('glm-4.7-runtime')
    expect(rows[0]).toContain('成功')
    expect(rows[0]).toContain('改了单价')
    // 每一个动作都有自己的词条，不是一股脑落成「其它操作」。
    expect(rows[1]).toContain('删模型')
    expect(rows[1]).toContain('失败')
  })

  it('没有记录说「暂无操作记录」，不是一块空白', async () => {
    const page = await mountLoaded()
    expect(page.getByText('暂无操作记录')).toBeTruthy()
  })

  it('读失败照原话，且**不**说「暂无操作记录」（那是把「没读到」说成「没有」）', async () => {
    getGatewayAudit.mockRejectedValue(new Error('audit 服务 503'))
    const page = mountPage()
    await page.findByText('GLM 4.7')

    expect(await page.findByText('audit 服务 503')).toBeTruthy()
    expect(page.queryByText('暂无操作记录')).toBeNull()
  })
})

describe('模型管理 · 页顶那条提示', () => {
  /** 打开删除确认框并按下框里那个「删除」。行里那一个和框里那一个同名（都是「删除」），
   *  所以第二个要从框里取 —— 在整页上取会取到两个。 */
  async function confirmDelete(page: ReturnType<typeof mountPage>) {
    await fireEvent.click(page.getByRole('button', { name: '删除' }))
    await page.findByText('删除模型')
    await fireEvent.click(within(page.getByRole('dialog')).getByRole('button', { name: '删除' }))
  }

  it('删除成功之后说清删掉的是哪一个：一条 role=status 的横条，可以关掉', async () => {
    const page = await mountLoaded()

    await confirmDelete(page)
    const flash = (await page.findByText('已删除「glm-4.7-runtime」')).closest('.amd__flash')!
    expect(flash.getAttribute('role')).toBe('status')

    await fireEvent.click(page.getByRole('button', { name: '关闭这条提示' }))
    await waitFor(() => expect(page.queryByText('已删除「glm-4.7-runtime」')).toBeNull())
  })

  it('写失败走的是同一条横条，但那是 role=alert：原话照说，表也还在', async () => {
    deleteGatewayModel.mockRejectedValue(new Error('网关拒绝：模型在 config 里，请改 config.yaml'))
    const page = await mountLoaded()

    await confirmDelete(page)
    const flash = (await page.findByText(/请改 config\.yaml/)).closest('.amd__flash')!
    expect(flash.getAttribute('role')).toBe('alert')
    // 写失败不该让整页看起来像没加载出来：模型表还在那儿。
    expect(page.getByText('GLM 4.7')).toBeTruthy()
  })
})
