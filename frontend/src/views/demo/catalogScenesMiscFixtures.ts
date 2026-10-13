/**
 * 预览站「小队与后台那几屏拆出来的半张画面」那一组（`catalogScenesMisc.ts` 的条目）读的数据。
 *
 * 全是产品里真会出现的形状，照着 `@/types` 与 `@/cx_types` 那几个类型造：小队照
 * `types/teams.ts` 的 `Team`，设备照 `cx_types.ts` 的 `MyDevice`，空间申请照
 * `types/spaces.ts` 的 `SpaceApplication`。飞书那一屏收的是一句话和一格表单状态，
 * 不是一个接口回包，所以直接写常量。
 *
 * 这里只出数据（外加几个把参数拼成 props 的小函数），不引运行时要装的东西：哪一份配
 * 哪个组件、每一格看什么，在 `catalogScenesMisc.ts`。
 */
import type { DeviceUser, MyDevice } from '@/cx_types'
import type { SpaceApplication, Team } from '@/types'
import type { User } from '@/types/users'

/** 一个人：`types/users.ts` 的 `User`。只填画得到的那几格。 */
export function user(over: Partial<User> = {}): User {
  return {
    id: 1,
    username: 'alice',
    nickname: '爱丽丝',
    avatarId: 3,
    intro: '',
    question_count: 0,
    answer_count: 0,
    ...over,
  }
}

// ---- 小队（MineView / ExploreView）-----------------------------------------

/** 一个小队：`types/teams.ts` 的 `Team`。 */
export function team(over: Partial<Team> = {}): Team {
  return {
    id: 1,
    handle: 'city-lab',
    name: '城市数据实验室',
    intro: '一起把城市数据用起来',
    avatarId: 3,
    owner: user({ id: 1, username: 'alice', nickname: '爱丽丝' }),
    admins: { total: 1, examples: [user({ id: 2, username: 'bob', nickname: '鲍勃' })] },
    members: { total: 4, examples: [user({ id: 3, username: 'carol', nickname: '卡罗尔' })] },
    role: 'OWNER',
    ...over,
  }
}

/** 「我的团队」那一列：两个真团队（个人团队在页面里被滤掉，这里不放）。 */
export const MY_TEAMS: Team[] = [
  team(),
  team({
    id: 2,
    handle: 'math-club',
    name: '数学建模社',
    intro: '每周一题，一起打磨',
    avatarId: 5,
    role: 'MEMBER',
  }),
]

/** 「发现小队」搜出来的一列。 */
export const SEARCH_TEAMS: Team[] = [
  team({ id: 7, handle: 'open-source', name: '开源小伙伴', intro: '把一个开源项目维护下去', avatarId: 8 }),
]

// ---- 设备（ComputeView）----------------------------------------------------

/** 一台属于自己的设备：`cx_types.ts` 的 `MyDevice`。 */
export function device(over: Partial<MyDevice> = {}): MyDevice {
  return {
    device_id: 'dev-3f9a21',
    name: '书房的 Mac mini',
    online: true,
    project_ids: [],
    team_ids: [1],
    screens: [],
    in_use: null,
    ...over,
  }
}

/** 屏幕后面那位队友正在做的事：`cx_types.ts` 的 `DeviceUser`。 */
export const IN_USE: DeviceUser = {
  project_id: 'p-clean',
  project_name: '数据清洗',
  topic_id: 't-2',
  topic_title: '把缺失值补上',
  agent_handle: 'cheese',
  agent_name: '芝士',
  agent_name_source: 'default',
}

/** 一台在线的、两台被别的项目用着的机器：分别是「在线」「有活干」两种行。 */
export const DEVICES: MyDevice[] = [
  device({ device_id: 'dev-3f9a21', name: '书房的 Mac mini', online: true, screens: [] }),
  device({
    device_id: 'dev-8b1c04',
    name: '客厅的 NUC',
    online: false,
    project_ids: ['p-clean'],
    team_ids: [],
    in_use: [IN_USE],
    attached_projects: [{ id: 'p-clean', name: '数据清洗' }],
  }),
]

/** 自己名下、还没注册给这个团队的那一台：菜单里列的就是它。 */
export const MY_DEVICES: MyDevice[] = [
  device({ device_id: 'dev-3f9a21', name: '书房的 Mac mini', team_ids: [] }),
  device({ device_id: 'dev-8b1c04', name: '客厅的 NUC', online: false, team_ids: [] }),
]

/** 已在团队里那一台，用来给「移出」那颗按钮判据。 */
export const TEAM_DEVICE_ID = 'dev-3f9a21'

// ---- 空间申请（AdminSpacesPageView）----------------------------------------

/** 一条开版申请：`types/spaces.ts` 的 `SpaceApplication`。 */
export function spaceApplication(over: Partial<SpaceApplication> = {}): SpaceApplication {
  return {
    id: 1,
    avatarId: 4,
    name: '计算机视觉兴趣组',
    intro: '看论文、跑 demo、一起复现',
    reviewStatus: 'PENDING',
    description: '看论文、跑 demo、一起复现',
    owner: 'dana',
    reviewReason: null,
    reviewedBy: null,
    reviewedAt: null,
    createdAt: '2026-09-28T02:00:00Z',
    ...over,
  }
}

/** 待审核那三堆：一条全新的、一条已经过的、一条被驳回的。 */
export const APPLICATIONS: SpaceApplication[] = [
  spaceApplication(),
  spaceApplication({
    id: 2,
    name: '嵌入式小组',
    intro: '从点灯到跑通一个 RTOS',
    description: '从点灯到跑通一个 RTOS',
    owner: 'erin',
    reviewStatus: 'APPROVED',
    reviewedBy: 'andy',
    reviewedAt: '2026-09-29T02:00:00Z',
  }),
  spaceApplication({
    id: 3,
    name: '量子计算读书会',
    intro: '从线性代数读到量子线路',
    description: '从几篇综述开始',
    owner: 'frank',
    reviewStatus: 'REJECTED',
    reviewReason: '请补充一份可以照着走的开课计划',
    reviewedBy: 'andy',
    reviewedAt: '2026-09-29T03:00:00Z',
  }),
]

// ---- 飞书应用（AdminIntegrationsPageView）----------------------------------

/** 已经配过一次：状态行说的是「已配置 · App ID · 版本」。 */
export const FEISHU_CONFIGURED: Record<string, unknown> = {
  loading: false,
  loadError: null,
  status: '已配置 · cli_platform · feishu',
  showStatus: true,
  configured: true,
  updated: '最后由 andy 更新',
  saving: false,
  saved: false,
  saveError: '',
  validationError: '',
  appId: 'cli_platform',
  appSecret: '',
  domain: 'feishu',
}

/** 还没配过：状态行说的是「还没配置」，两格空着等人填。 */
export const FEISHU_UNCONFIGURED: Record<string, unknown> = {
  ...FEISHU_CONFIGURED,
  status: '还没配置',
  configured: false,
  updated: '',
  appId: '',
}

// ---- 外站题账号（AdminExternalProblemPageView）----------------------------

/** 已经配过一次：状态行说的是「已配置 · 账号 · 来源」。密码永远是空的（只写不回显）。
 *  这里用的是占位账号，不是任何真账号。 */
export const EXTERNAL_PROBLEM_CONFIGURED: Record<string, unknown> = {
  loading: false,
  loadError: null,
  status: '已配置 · teacher01 · yoj',
  showStatus: true,
  configured: true,
  updated: '最后由 andy 更新',
  saving: false,
  saved: false,
  saveError: '',
  validationError: '',
  username: 'teacher01',
  password: '',
}

/** 还没配过：状态行说的是「还没配置」，两格空着等人填。 */
export const EXTERNAL_PROBLEM_UNCONFIGURED: Record<string, unknown> = {
  ...EXTERNAL_PROBLEM_CONFIGURED,
  status: '还没配置',
  configured: false,
  updated: '',
  username: '',
}
