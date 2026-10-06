/**
 * 后台布局预览用的样例数据：模型管理、空间申请、棘轮三页（临时，只进预览构建）。
 *
 * 全部是编的：模型名、项目名、金额、申请人都不对应真实数据。刻意放进几条「长内容」
 * —— 很长的模型名、很长的申请简介、没定价的模型、被停用的模型 —— 布局要在这些行上
 * 也站得住，不能只在整齐的样例上好看。
 */
import type { GatewayAuditEntry, GatewayModelInfo, GatewayProject, GatewayUsageNumbers } from '@/api'
import type { SpaceApplication } from '@/network/api/spaces/types'
import type { RatchetArea, RatchetBoard, RatchetCheck, RatchetPoint } from '@/views/admin/ratchetApi'

import { featureReport } from '@/proto-feature-fixtures'

const DAY = 86400_000
const ago = (ms: number) => new Date(Date.now() - ms).toISOString()
const day = (offset: number) => new Date(Date.now() - offset * DAY).toISOString().slice(0, 10)

function usage(spend: number, requests: number, tokens: number, failed = 0): GatewayUsageNumbers {
  return {
    spend_usd: spend,
    requests,
    failed_requests: failed,
    prompt_tokens: Math.round(tokens * 0.7),
    completion_tokens: Math.round(tokens * 0.2),
    cache_read_tokens: Math.round(tokens * 0.1),
    total_tokens: tokens,
  }
}

function model(
  name: string,
  label: string,
  host: string,
  u: GatewayUsageNumbers,
  extra: Partial<GatewayModelInfo> = {}
): GatewayModelInfo {
  return {
    name,
    model_id: name,
    label,
    origin: 'config',
    blocked: false,
    selectable: true,
    priced: true,
    offered: true,
    blocked_reasons: [],
    unpriced_reason: null,
    upstream: { model: name, host, provider: host.split('.')[0] },
    prices: { input: 3, output: 15, cache_read: 0.3, cache_creation: 3.75 },
    capabilities: { reasoning: true, vision: true },
    usage: u,
    series: [3, 5, 4, 8, 6, 9, 7].map((n) => n * (u.requests / 40)),
    ...extra,
  }
}

const MODELS: GatewayModelInfo[] = [
  model('claude-opus-5-5', 'Claude Opus 5.5', 'api.anthropic.com', usage(412.36, 1840, 96_400_000, 3)),
  model('claude-sonnet-5', 'Claude Sonnet 5', 'api.anthropic.com', usage(188.02, 5210, 142_000_000, 11)),
  model('glm-4.6', 'GLM 4.6', 'open.bigmodel.cn', usage(21.7, 3380, 58_900_000, 26), {
    prices: { input: 0.7, output: 0.7 },
  }),
  model('gpt-5.2-codex', 'GPT-5.2 Codex', 'api.openai.com', usage(64.9, 902, 21_300_000, 0), { origin: 'runtime' }),
  model(
    'qwen3-coder-480b-a35b-instruct-long-context-preview-2026-09',
    'Qwen3 Coder 480B（长上下文预览版，名字很长的样例）',
    'dashscope.aliyuncs.com',
    usage(0, 12, 88_000, 4),
    { priced: false, offered: false, unpriced_reason: '上游没有公布价格', prices: {} }
  ),
  model('deepseek-v4', 'DeepSeek V4', 'api.deepseek.com', usage(0, 0, 0), {
    blocked: true,
    offered: false,
    blocked_reasons: ['blocked'],
  }),
]

function project(id: string, name: string, spend: number, budget: number | null, used: number): GatewayProject {
  return {
    project_id: id,
    name,
    key_alias: `proj-${id.slice(0, 6)}`,
    has_key: true,
    gateway_spend_usd: spend,
    max_budget_usd: budget,
    budget_derived_usd: budget,
    budget_override_usd: null,
    credits: { total: 20000, used, remaining: 20000 - used, unlimited: budget === null },
    usage: usage(spend, Math.round(spend * 9), Math.round(spend * 230_000)),
  }
}

const PROJECTS: GatewayProject[] = [
  project('a1b2c3d4e5', 'Cheese 平台代码', 402.1, null, 0),
  project('b2c3d4e5f6', '计算机系统基础 · 2026 秋（100 人课程空间的共享额度，名字很长的样例）', 188.4, 400, 9420),
  project('c3d4e5f6a7', '推荐算法原型', 61.5, 100, 3075),
  project('d4e5f6a7b8', '人大三方向资料调研', 34.9, 30, 1745),
]

const AUDIT: GatewayAuditEntry[] = [
  {
    created_at: ago(2 * 3600_000),
    actor_handle: 'andy',
    action: 'model.block',
    target: 'deepseek-v4',
    result: 'ok',
    detail: '上游连续 3 小时超时率超过 20%，先停用，恢复后再上架。',
    before: { blocked: false },
    after: { blocked: true },
  },
  {
    created_at: ago(DAY),
    actor_handle: 'wangchangxin',
    action: 'project.budget',
    target: '计算机系统基础 · 2026 秋',
    result: 'ok',
    detail: null,
    before: { max_budget_usd: 300 },
    after: { max_budget_usd: 400 },
  },
  {
    created_at: ago(3 * DAY),
    actor_handle: 'andylizf',
    action: 'model.create',
    target: 'gpt-5.2-codex',
    result: 'failed',
    detail: '上游返回 400：model not found（样例错误）',
    before: null,
    after: null,
  },
]

function application(
  id: number,
  name: string,
  owner: string,
  intro: string,
  status: SpaceApplication['reviewStatus'],
  hoursAgo: number
): SpaceApplication {
  return {
    id,
    avatarId: null,
    name,
    intro,
    reviewStatus: status,
    description: intro,
    owner,
    reviewReason: status === 'REJECTED' ? '简介里没说明题目来源，补充后可重新提交。' : null,
    reviewedBy: status === 'PENDING' ? null : 'andy',
    reviewedAt: status === 'PENDING' ? null : ago(hoursAgo * 3600_000 - 3600_000),
    createdAt: ago(hoursAgo * 3600_000),
  }
}

const APPLICATIONS: SpaceApplication[] = [
  application(41, '算法竞赛周练', 'maxiaoyu', '每周一套题，赛后讲评。面向校队新人。', 'PENDING', 3),
  application(
    42,
    '计算机系统基础 2026 秋季学期实验题目板（含 4 个大作业与每周小测的全部题目，名字很长的样例）',
    'caisongyang',
    '本学期约 100 名学生使用。题目按周发布，每道题附评分脚本；助教 4 人负责批改。申请公开是为了让往届学生也能看到讲评。这段简介故意写得很长，用来看列表在长内容下会不会被撑坏。',
    'PENDING',
    20
  ),
  application(43, '数据库课程设计', 'chiruotong', '期末大作业选题与中期检查。', 'PENDING', 30),
  application(38, '前端组件练习', 'pengwenbo', '组件拆分与测试的练习题。', 'APPROVED', 72),
  application(36, '随便建建', 'n1ctheboy', '测试一下。', 'REJECTED', 96),
]

/* ---- 棘轮（`/admin/ratchet`）------------------------------------------------
 *
 * 这一份**几乎全是真的**：每道检查的 id、区域、方向、实际、冻结、豁免、规则指纹，
 * 以及那 12 次采集的提交、时刻、run 链接和每个点上的读数，都逐字抄自 2026-10-01
 * 11:48Z 那次采集（run 36857342568 / 提交 9467ffb0）。
 *
 * 抄而不是编，是因为这一页要给人判断的正是**这些摆法读不读得懂** —— 数字换成编的，
 * 「0 和没量到分不分得清」这类问题就看不出来了。历史只取 12 次（真的归档有 60 次），
 * 所以行尾那条小线是**压缩过的走势**，不是真实形状。
 *
 * 历史经过抽样，旧规则指纹为样例值；不作为线上报告。
 */

/** 这 12 次采集的提交、时刻、以及 CI run 的编号 —— 逐字抄自归档，所以时间线上
 *  每个短 sha 点开都是那一次真的 run。 */
const RATCHET_RUNS: [commit: string, at: string, run: string][] = [
  ['c5da2b147ec417f10725ce397682acbec43dad5e', '2026-09-30T17:26:06+00:00', '36750988019'],
  ['f45be47bef9c50b4daa49710fce97946eda7635b', '2026-09-30T18:54:55+00:00', '36761593999'],
  ['9745b40718af7556337c3cac16be80421b811135', '2026-09-30T21:22:19+00:00', '36778710410'],
  ['149615b38727cf6bdf9c16075a9777e7f31069a2', '2026-09-30T23:04:40+00:00', '36788862659'],
  ['9b52e40dff873c79e74eeb3218e5b40425acc292', '2026-10-01T02:49:51+00:00', '36807453565'],
  ['a523012777e4dc398ab3899225a6a293dc18187e', '2026-10-01T04:59:54+00:00', '36817462966'],
  ['83d3efa2e5c9da78be0278d25b664459fc411211', '2026-10-01T06:26:42+00:00', '36824510067'],
  ['87a44b9499b2417d6e1f81826172f4af1fc3f8b4', '2026-10-01T07:16:01+00:00', '36829106324'],
  ['27090463e498923678f0292ae0d0c5ed079d9e81', '2026-10-01T09:53:22+00:00', '36845275654'],
  ['a8b412731db545e6b4d90e5e33b866ed5be017da', '2026-10-01T10:39:18+00:00', '36850157556'],
  ['da6421fce537bc43b179fad52a6b13481618ed1c', '2026-10-01T11:24:18+00:00', '36854813696'],
  ['9467ffb053deddaf0318d7bd29e0019e54c853a7', '2026-10-01T11:48:36+00:00', '36857342568'],
]

/** 整棵树的超限情况。**只有 `file-sizes` 这一道带**，因为它是 diff 口径的闸门：
 *  `actual` 数「这次改过、并且超了的文件」，采集跑在 main 的合并提交上时那个数是 0，
 *  读起来像「树上没有超限文件」。这一块才是「树上到底超了多少」。 */
const RATCHET_TREE = {
  offenders: 16,
  excess_lines: 18695,
  caps: [
    { prefix: 'frontend/src/', cap: 1000, judged: 1283, over_cap: 4, excess_lines: 5416 },
    { prefix: 'backend/app/', cap: 1500, judged: 742, over_cap: 12, excess_lines: 13279 },
  ],
}

/** 一道检查现在的样子。字段名照 `RatchetCheck`；`runs` 是那 12 个点各一格，顺序与
 *  `RATCHET_RUNS` 一致：[实际, 冻结, 豁免条数, 规则变了, 新增豁免]。
 *  `ruleAt` 是规则指纹换过的那一格（-1 = 这 12 次里没换过）——指纹本身按它分两段，
 *  走势线就是在那里断开的。 */
const RATCHET_CHECKS: (Omit<RatchetCheck, 'points'> & {
  runs: [number | null, number | null, number | null, boolean, number | null][]
  ruleAt: number
})[] = [
  {
    id: 'scene-ratchet',
    area: '场景',
    better: 'down',
    direction: 'improving',
    status: 'pass',
    actual: 121,
    frozen: 121,
    stale_count: 0,
    rule_fingerprint: 'cb4fc043fb140f970a0e99086b35a149eea06f69d1c9305369c97785f8b14925',
    ruleAt: -1,
    stale: [],
    runs: [
      [124, 124, 0, false, null],
      [123, 123, 0, false, null],
      [123, 123, 0, false, null],
      [123, 123, 0, false, null],
      [122, 123, 1, false, null],
      [122, 123, 1, false, null],
      [121, 122, 1, false, null],
      [121, 122, 1, false, null],
      [121, 122, 1, false, null],
      [121, 122, 1, false, null],
      [121, 121, 0, false, null],
      [121, 121, 0, false, null],
    ],
  },
  {
    id: 'fe-boundary',
    area: '边界',
    better: 'down',
    direction: 'improving',
    status: 'pass',
    actual: 104,
    frozen: 104,
    stale_count: 0,
    rule_fingerprint: '9651b9665f4baeacb32ae51b5fb2b2479e138b9ce4b4fc0da9a58f3c205f7c1a',
    ruleAt: -1,
    stale: [],
    runs: [
      [109, 109, 0, false, null],
      [109, 109, 0, false, null],
      [109, 109, 0, false, null],
      [109, 109, 0, false, null],
      [108, 109, 1, false, null],
      [108, 109, 1, false, null],
      [104, 105, 1, false, null],
      [104, 105, 1, false, null],
      [104, 105, 1, false, null],
      [104, 105, 1, false, null],
      [104, 104, 0, false, null],
      [104, 104, 0, false, null],
    ],
  },
  {
    id: 'be-contracts',
    area: '边界',
    better: 'down',
    direction: 'improving',
    status: 'pass',
    actual: 253,
    frozen: 256,
    stale_count: 3,
    rule_fingerprint: '786431f0a72cccd5e180a1912970a5715bcd0047ccea6f2d3ba8caf05c95ce1b',
    ruleAt: -1,
    stale: [
      {
        why: 'No matches for ignored import app.api.routes.spaces -> app.domain.task.models.',
        file: 'routes-touch-no-models',
        actual: 0,
        frozen: 1,
      },
      {
        why: 'No matches for ignored import app.api.routes.spaces -> app.domain.teaching.models.',
        file: 'routes-touch-no-models',
        actual: 0,
        frozen: 1,
      },
      {
        why: 'No matches for ignored import app.domain.machine.warm -> app.domain.machine.services.',
        file: 'domains-acyclic',
        actual: 0,
        frozen: 1,
      },
    ],
    runs: [
      [255, 257, 2, false, null],
      [254, 256, 2, false, null],
      [254, 256, 2, false, null],
      [254, 256, 2, false, null],
      [254, 256, 2, false, null],
      [254, 256, 2, false, null],
      [253, 256, 3, false, null],
      [253, 256, 3, false, null],
      [253, 256, 3, false, null],
      [253, 256, 3, false, null],
      [253, 256, 3, false, null],
      [253, 256, 3, false, null],
    ],
  },
  {
    id: 'domain-import-guard',
    area: '边界',
    better: 'down',
    direction: 'improving',
    status: 'pass',
    actual: 153,
    frozen: 153,
    stale_count: 0,
    rule_fingerprint: '48b91e7901cfb7f9170a7bf9db5cb57036b39672ff5e3c945b57ee58cce21118',
    ruleAt: -1,
    stale: [],
    runs: [
      [155, 155, 0, false, null],
      [152, 152, 0, false, null],
      [152, 152, 0, false, null],
      [152, 152, 0, false, null],
      [152, 152, 0, false, null],
      [152, 152, 0, false, null],
      [150, 150, 0, false, null],
      [150, 150, 0, false, null],
      [150, 150, 0, false, null],
      [153, 153, 0, false, 3],
      [153, 153, 0, false, null],
      [153, 153, 0, false, null],
    ],
  },
  {
    id: 'harness-boundary',
    area: '边界',
    better: 'down',
    direction: 'flat',
    status: 'pass',
    actual: 6,
    frozen: 6,
    stale_count: 0,
    rule_fingerprint: '70fa628016196611d7bbe5197afc545cb9be9cbc41963f81ea451e95ad9b3e01',
    ruleAt: 8,
    stale: [],
    runs: [
      [6, 6, 0, false, null],
      [6, 6, 0, false, null],
      [6, 6, 0, false, null],
      [6, 6, 0, false, null],
      [6, 6, 0, false, null],
      [6, 6, 0, false, null],
      [6, 6, 0, false, null],
      [6, 6, 0, false, null],
      [6, 6, 0, true, null],
      [6, 6, 0, false, null],
      [6, 6, 0, false, null],
      [6, 6, 0, false, null],
    ],
  },
  {
    id: 'is-private-read-points',
    area: '边界',
    better: 'down',
    direction: 'flat',
    status: 'pass',
    actual: 20,
    frozen: 20,
    stale_count: 0,
    rule_fingerprint: 'b5a354a2b2f7c12a70b2abfdc27e23fc790e639cda0f8c4feac532f987462d32',
    ruleAt: -1,
    stale: [],
    runs: [
      [20, 20, 0, false, null],
      [20, 20, 0, false, null],
      [20, 20, 0, false, null],
      [20, 20, 0, false, null],
      [20, 20, 0, false, null],
      [20, 20, 0, false, null],
      [20, 20, 0, false, null],
      [20, 20, 0, false, null],
      [20, 20, 0, false, null],
      [20, 20, 0, false, null],
      [20, 20, 0, false, null],
      [20, 20, 0, false, null],
    ],
  },
  {
    id: 'file-sizes',
    area: '规模',
    better: 'down',
    direction: 'flat',
    status: 'pass',
    actual: 0,
    frozen: 0,
    stale_count: 0,
    rule_fingerprint: 'a161682ef7d901cd16ee4d25ef54b199743a9f7a7aae327524249e8053ff365b',
    ruleAt: -1,
    stale: [],
    tree: RATCHET_TREE,
    runs: [
      [0, 0, 0, false, null],
      [0, 0, 0, false, null],
      [0, 0, 0, false, null],
      [0, 0, 0, false, null],
      [0, 0, 0, false, null],
      [0, 0, 0, false, null],
      [0, 0, 0, false, null],
      [0, 0, 0, false, null],
      [0, 0, 0, false, null],
      [0, 0, 0, false, null],
      [0, 0, 0, false, null],
      [0, 0, 0, false, null],
    ],
  },
  {
    id: 'vue-tsc',
    area: '类型与样式',
    better: 'down',
    direction: 'flat',
    status: 'pass',
    actual: 0,
    frozen: 0,
    stale_count: 0,
    rule_fingerprint: '6b45919bd1a9ed1b0d0584c1671cca7f332806a161f35d8cf1357359eaf6dcc5',
    ruleAt: -1,
    stale: [],
    runs: [
      [0, 0, 0, false, null],
      [0, 0, 0, false, null],
      [0, 0, 0, false, null],
      [0, 0, 0, false, null],
      [0, 0, 0, false, null],
      [0, 0, 0, false, null],
      [0, 0, 0, false, null],
      [0, 0, 0, false, null],
      [0, 0, 0, false, null],
      [0, 0, 0, false, null],
      [0, 0, 0, false, null],
      [0, 0, 0, false, null],
    ],
  },
  {
    id: 'stylelint-tokens',
    area: '类型与样式',
    better: 'down',
    direction: 'improving',
    status: 'pass',
    actual: 14,
    frozen: 14,
    stale_count: 0,
    rule_fingerprint: '15ebb18a30af2773bb19ba1ca2445f69632c4a2a4be1b7a69df9e35b3bcc8763',
    ruleAt: 1,
    stale: [],
    runs: [
      [30, 30, 0, false, null],
      [30, 30, 0, true, null],
      [29, 29, 0, false, null],
      [29, 29, 0, false, null],
      [29, 29, 0, false, null],
      [29, 29, 0, false, null],
      [14, 14, 0, false, null],
      [14, 14, 0, false, null],
      [14, 14, 0, false, null],
      [14, 14, 0, false, null],
      [14, 14, 0, false, null],
      [14, 14, 0, false, null],
    ],
  },
  {
    id: 'palette',
    area: '类型与样式',
    better: 'down',
    direction: 'improving',
    status: 'pass',
    actual: 4,
    frozen: 6,
    stale_count: 2,
    rule_fingerprint: '89614c999d22295b0c315522a7fe772e725816eb593d554432e495315092114d',
    ruleAt: -1,
    stale: [
      { why: 'no hits left in the tree', file: 'src/components/common/ColorSelector.vue', actual: 0, frozen: 1 },
      { why: 'no hits left in the tree', file: 'src/layouts/account/Account.vue', actual: 0, frozen: 1 },
    ],
    runs: [
      [5, 6, 1, false, null],
      [5, 6, 1, false, null],
      [5, 6, 1, false, null],
      [4, 6, 2, false, null],
      [4, 6, 2, false, null],
      [4, 6, 2, false, null],
      [4, 6, 2, false, null],
      [4, 6, 2, false, null],
      [4, 6, 2, false, null],
      [4, 6, 2, false, null],
      [4, 6, 2, false, null],
      [4, 6, 2, false, null],
    ],
  },
]

/** 明细只留前几条：展开那一块是 `max-height: 320px` 的滚动区，前六条就够看出它滚不滚。 */
const RATCHET_DETAILS: Record<string, unknown[]> = {
  'scene-ratchet': [
    {
      file: 'src/components/panels/PanelCard.vue',
      grade: 'C',
      reasons: [
        'imports the API layer (frontend/src/api.ts)',
        'reaches the API layer through frontend/src/components/room/composables/useRoomSocket.ts',
        'reaches the API layer through frontend/src/components/room/RoomNotice.vue',
        'reaches the API layer through frontend/src/components/TopicAcceptCard.vue',
        'reaches the API layer through frontend/src/components/common/UserRefLink.vue',
      ],
    },
    {
      file: 'src/components/panels/PanelChanges.vue',
      grade: 'C',
      reasons: [
        'reaches the API layer through frontend/src/composables/usePanelChanges.ts',
        'reaches the API layer through frontend/src/components/panels/PanelChangesView.vue',
      ],
    },
    {
      file: 'src/components/panels/PanelChangesView.vue',
      grade: 'C',
      reasons: ['reaches the API layer through frontend/src/components/panels/preview/RevisionList.vue'],
    },
    {
      file: 'src/components/panels/PanelDoc.vue',
      grade: 'C',
      reasons: [
        'reaches the API layer through frontend/src/composables/usePanelDoc.ts',
        'reaches the API layer through frontend/src/components/panels/PanelDocView.vue',
      ],
    },
    {
      file: 'src/components/panels/PanelDocView.vue',
      grade: 'C',
      reasons: ['reaches the API layer through frontend/src/components/panels/doc/OverviewAuto.vue'],
    },
    {
      file: 'src/components/panels/PanelOverview.vue',
      grade: 'C',
      reasons: [
        'reaches the API layer through frontend/src/components/panels/PanelCard.vue',
        'reaches the API layer through frontend/src/components/panels/PanelProgress.vue',
        'reaches the API layer through frontend/src/components/panels/TaskProgress.vue',
        'reaches the API layer through frontend/src/components/panels/PanelDoc.vue',
      ],
    },
  ],
  'fe-boundary': [
    { file: 'src/components/ArchiveProjectDialog.vue', count: 2 },
    { file: 'src/components/ArtifactManifest.vue', count: 2 },
    { file: 'src/components/ArtifactVersionPreview.vue', count: 2 },
    { file: 'src/components/AttachmentDocThumb.vue', count: 1 },
    { file: 'src/components/AttachmentImage.vue', count: 1 },
    { file: 'src/components/DeviceLiveViewer.vue', count: 1 },
  ],
  'be-contracts': [
    {
      file: 'api-domain-core',
      kept: true,
      name: 'C1 layers: api -> domain -> core, no imports upward',
      stale: 0,
      actual: 27,
      frozen: 27,
    },
    {
      file: 'routes-touch-no-models',
      kept: true,
      name: "C2 routes do not import a domain's models directly",
      stale: 2,
      actual: 53,
      frozen: 55,
    },
    {
      file: 'domains-acyclic',
      kept: true,
      name: 'C3 sibling domains under app.domain are acyclic',
      stale: 1,
      actual: 173,
      frozen: 174,
    },
  ],
  'domain-import-guard': [],
  'harness-boundary': [],
  'is-private-read-points': [],
  'file-sizes': [],
  'vue-tsc': [],
  'stylelint-tokens': [
    { file: 'src/components/DeviceLiveViewer.vue', count: 3 },
    { file: 'src/components/TopicMembers.vue', count: 2 },
    { file: 'src/components/TopicSidebar.vue', count: 1 },
    { file: 'src/components/common/AvatarUploader.vue', count: 2 },
    { file: 'src/components/common/OfflineBanner.vue', count: 1 },
    { file: 'src/components/common/UserAvatar.vue', count: 1 },
  ],
  palette: [
    { file: 'src/components/common/AvatarUploader.vue', count: 3 },
    { file: 'src/views/spaces/Index.vue', count: 1 },
  ],
}

/** 那个洞：`be-contracts` 这一次采集整个失败了。它不进走势线，时间线上标「采集失败」，
 *  「实际」那一格写「没跑到」—— 三处说的都是同一件事：**这次不知道**，不是 0。 */

/** 换过规则指纹的那两道，换之前的指纹长什么样。两段必须真的不同 —— 走势线是靠相邻
 *  两格的指纹不相等来断开的，写成一样的话线不会断，而页面上看不出来它该断。 */
const RATCHET_OLD_RULE = '0f1e2d3c4b5a69788796a5b4c3d2e1f00f1e2d3c4b5a69788796a5b4c3d2e1f0'

const RATCHET_REPO = 'SageSeekerSociety/cheese'
const runUrl = (run: string) => `https://github.com/${RATCHET_REPO}/actions/runs/${run}`

/** 把一份检查摊成 12 个点。明细只挂在最后那一格上：`detailsOf()` 读的就是它，
 *  每一格都挂一遍会让这个文件大出十倍，而页面上一点区别都没有。 */
function ratchetPoints(check: (typeof RATCHET_CHECKS)[number]): RatchetPoint[] {
  const last = check.runs.length - 1
  return check.runs.map(([actual, frozen, stale, ruleChanged, newExemptions], slot) => {
    const [commit, at, run] = RATCHET_RUNS[slot]
    return {
      commit,
      collected_at: at,
      run_url: runUrl(run),
      collection: 'ok',
      status: check.status,
      actual,
      frozen,
      stale_count: stale,
      rule_fingerprint: check.ruleAt >= 0 && slot < check.ruleAt ? RATCHET_OLD_RULE : check.rule_fingerprint,
      rule_changed: ruleChanged,
      new_exemptions: newExemptions,
      details: slot === last ? RATCHET_DETAILS[check.id] ?? [] : [],
      reason: null,
    }
  })
}

/** 分区顺序跟着服务端来：它按采集器登记表的顺序拼，`场景` 在最前。 */
const RATCHET_AREAS: RatchetArea[] = []
for (const raw of RATCHET_CHECKS) {
  const area = RATCHET_AREAS.find((one) => one.area === raw.area)
  const entry: RatchetCheck = {
    id: raw.id,
    area: raw.area,
    better: raw.better,
    direction: raw.direction,
    status: raw.status,
    actual: raw.actual,
    frozen: raw.frozen,
    stale_count: raw.stale_count,
    stale: raw.stale,
    rule_fingerprint: raw.rule_fingerprint,
    tree: raw.tree,
    points: ratchetPoints(raw),
  }
  if (area) area.checks.push(entry)
  else RATCHET_AREAS.push({ area: raw.area, checks: [entry] })
}

const RATCHET_LAST = RATCHET_RUNS[RATCHET_RUNS.length - 1]

/** 归档里现在的样子。**不碰 GitHub** —— 这一页读的是已经存下来的那些点。 */
const RATCHET_BOARD: RatchetBoard = {
  repo: RATCHET_REPO,
  generated_at: new Date().toISOString(),
  deployed_commit: RATCHET_LAST[0],
  collected_commit: RATCHET_LAST[0],
  collected_at: RATCHET_LAST[1],
  run_url: runUrl(RATCHET_LAST[2]),
  collection: 'ok',
  points: RATCHET_RUNS.length,
  // 归档里其实有 90 次，这一屏只带回最近 12 个点 —— 两个数在页面上各说各的话，
  // 所以假数据里也得是两个不同的数（相等的话那句话读起来永远是「全都在这儿」）。
  total_stored: 90,
  collections: RATCHET_RUNS.map(([commit, at, run]) => ({
    commit,
    collected_at: at,
    run_url: runUrl(run),
    collection: 'ok',
    reason: null,
  })),
  areas: RATCHET_AREAS,
}

/** 返回 `undefined` 表示「不是这几页的接口」，交回给反馈那份假数据。`payload` 是
 *  已经解析过的请求体（只有写接口用得到）。 */
export function adminRoutes(
  path: string,
  method: string,
  url: URL,
  payload: Record<string, unknown> = {}
): { data: unknown } | undefined {
  if (path === '/admin/gateway/models' && method === 'GET') {
    const days = Number(url.searchParams.get('days') ?? 7)
    const totals = MODELS.reduce(
      (sum, m) => {
        for (const key of Object.keys(sum) as (keyof GatewayUsageNumbers)[]) sum[key] += m.usage[key]
        return sum
      },
      usage(0, 0, 0)
    )
    return {
      data: {
        gateway: { reachable: true, readiness: 'ok', admin_configured: true, detail: null, fetched_at: ago(60_000) },
        window: { days, start_date: day(days - 1), end_date: day(0) },
        totals,
        models: MODELS,
      },
    }
  }
  if (path === '/admin/gateway/projects' && method === 'GET') {
    const days = Number(url.searchParams.get('days') ?? 7)
    return {
      data: {
        window: { days, start_date: day(days - 1), end_date: day(0) },
        projects: PROJECTS,
        totals: { projects: PROJECTS.length, with_key: PROJECTS.length, over_budget: 1, unlimited: 1 },
      },
    }
  }
  if (path === '/admin/gateway/audit' && method === 'GET') return { data: { items: AUDIT } }
  // 棘轮：读归档（不碰 GitHub），和去 CI 拉一次新的。刷新的响应**多一块 `refresh`**
  // —— `listed` 是 CI 上看到几份工件、`stored` 是这次新入库几份，两个数分开是因为
  // 「CI 上有 50 份」和「这次新存了 1 份」读起来像同一件事、做起来完全是两回事。
  if (path === '/admin/ratchet' && method === 'GET') return { data: RATCHET_BOARD }
  if (path === '/admin/ratchet/refresh' && method === 'POST') {
    return {
      data: {
        ...RATCHET_BOARD,
        refresh: {
          repo: RATCHET_REPO,
          listed: 91,
          stored: 1,
          already_stored: 90,
          unreadable: 0,
          failed: 0,
          error: '',
        },
      },
    }
  }
  if (path === '/admin/spaces' && method === 'GET') {
    const status = (url.searchParams.get('status') ?? 'PENDING').toUpperCase()
    return { data: { items: APPLICATIONS.filter((a) => a.reviewStatus === status) } }
  }
  if (/^\/admin\/spaces\/\d+\/review$/.test(path) && method === 'POST') {
    // 通过 / 驳回：预览里把这一条改在样例上，好让「点了以后那一行去哪了」也是真的
    // （待审 → 已通过）。审查意见跟着一起落，驳回时那一栏才有话可看。
    const id = Number(path.split('/')[3])
    const row = APPLICATIONS.find((a) => a.id === id)
    if (!row) return undefined
    row.reviewStatus = payload.approved ? 'APPROVED' : 'REJECTED'
    row.reviewReason = typeof payload.reason === 'string' && payload.reason ? payload.reason : null
    row.reviewedBy = 'andy'
    row.reviewedAt = new Date().toISOString()
    return { data: { item: row } }
  }
  // 功能数据的目录。**只有 id + 标题 + 一句话**，一个数字都没有 —— 那是它的设计
  // （数字在各自的功能页上），不是还没做。值照抄服务端注册表
  // （`backend/app/domain/feature_stats/features/docs_assistant.py`）。
  if (path === '/admin/feature-stats' && method === 'GET') {
    return {
      data: {
        features: [
          { id: 'docs-assistant', title: '问芝士', summary: '文档站的问答助手' },
          { id: 'task-naming', title: '智能命名', summary: '任务自动命名的调用与标题变更' },
        ],
      },
    }
  }
  return method === 'GET' ? featureReport(path, url) : undefined
}
