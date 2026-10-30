/**
 * 项目设置页（`views/ProjectSettingsView.vue`）里那五件 `Project*Settings` 在预览站里
 * 的条目。
 *
 * 和别的条目没有两样（「这是什么 / 在哪儿 / 需要哪几样 / 看哪几格」，规矩见
 * `catalog.ts`），单独一份是因为 `catalog.ts` 已经八百多行、`catalogSettings.ts` 也
 * 三百多行，五条塞进任何一份都会顶到 `frontend/src` 那一千行的上限 —— 和
 * `catalogQueue.ts`、`catalogSettings.ts` 同一个理由。数据见
 * `catalogSettingsDataFixtures.ts`。
 *
 * 为什么这五件值得一站：拆之前数据、保存、轮询、授权回跳全长在组件里（各自
 * `import ../api`，`frontend_grade.py` 的 C 级），想看哪一块都得把整页拉起来。现在每一件
 * 都只吃 props、只往上发事件（A 级，取数全在 `composables/useProject*Settings.ts` 里、
 * 由设置页接线），于是每一件、每一种有意义的状态（读到 / 读失败 / 只读 / 编辑中 /
 * 保存中 / 保存好了）都能单独摆在预览站里。
 *
 * 不登记 `ProjectSettingsView` 整页：它现在只剩接线（实例化那五个 composable、把
 * 把 `DeviceSessionsSwitch` 作为设备入口组件传入、按 `section` 门控取数），
 * 而且它自己不是 A 级，在场景棘轮眼里是那棵树上的欠债 —— 场景是路由页和
 * `components/panels/**`，整页不是给预览站单独画的。
 *
 * 这里的 `CatalogEntry` 是 type-only 引用：`catalog.ts` 反过来要 `SETTINGS_DATA_ENTRIES`
 * 这个值，运行时不构成循环。
 */
import type { CatalogEntry, CatalogNeed } from './catalog'

import { defineComponent, h, markRaw } from 'vue'

import {
  computeState,
  defaultModelState,
  envDiagnosis,
  envInfo,
  envStatus,
  mcpList,
  mcpLocal,
  mcpServer,
  modelChoice,
  taskNamingState,
} from './catalogSettingsDataFixtures'

import MoveProjectDialog from '@/components/MoveProjectDialog.vue'
import ProjectComputeSettings from '@/components/ProjectComputeSettings.vue'
import ProjectDefaultModelSettings from '@/components/ProjectDefaultModelSettings.vue'
import ProjectEnvironmentSettings from '@/components/ProjectEnvironmentSettings.vue'
import ProjectMcpSettings from '@/components/ProjectMcpSettings.vue'
import ProjectTaskNamingSettings from '@/components/ProjectTaskNamingSettings.vue'
import ProjectCreditBudget from '@/components/settings/ProjectCreditBudget.vue'
import ProjectRoleDetail from '@/components/settings/ProjectRoleDetail.vue'
import ProjectRoleList from '@/components/settings/ProjectRoleList.vue'
import RoleCreateDialog from '@/components/settings/RoleCreateDialog.vue'
import RoleDeleteDialog from '@/components/settings/RoleDeleteDialog.vue'

// i18n-data: role names a demo project chose; the catalog shows them as user data
const DEMO_TESTER = '内测用户'
const ALL_BUT_PEOPLE = [
  'edit_settings',
  'export_project',
  'create_channels',
  'manage_channels',
  'speak',
  'ask_agent',
  'tasks',
  'edit_content',
  'share_documents',
  'comment',
  'configure_agents',
]
const DEMO_ROLES = [
  {
    key: 'admin',
    name: null,
    builtin: 'admin' as const,
    template: null,
    permissions: ['manage_members', 'change_roles', ...ALL_BUT_PEOPLE],
    defaults: ['manage_members', 'change_roles', ...ALL_BUT_PEOPLE],
    holders: 2,
  },
  {
    key: 'member',
    name: null,
    builtin: 'member' as const,
    template: null,
    permissions: ALL_BUT_PEOPLE.filter((p) => p !== 'edit_settings' && p !== 'manage_channels'),
    defaults: ALL_BUT_PEOPLE.filter((p) => p !== 'edit_settings' && p !== 'manage_channels'),
    holders: 4,
  },
  {
    key: '5b0d6c1e-0000-4000-8000-000000000001',
    name: DEMO_TESTER,
    builtin: null,
    template: null,
    permissions: ['speak', 'ask_agent', 'comment'],
    defaults: null,
    holders: 12,
  },
]

/** 五件都吃 vuetify。`ProjectMcpSettings` 多一样 i18n：它用 `<i18n-t>`，那一个由插件
 *  全局注册，不装就解析不了、挂起来会警告。 */
const UI: CatalogNeed[] = ['vuetify']
const UI_I18N: CatalogNeed[] = ['vuetify', 'i18n']
const DeviceSessionsPreview = markRaw(defineComponent({ setup: () => () => h('span', '设备会话入口') }))

export const SETTINGS_DATA_ENTRIES: CatalogEntry[] = [
  {
    id: 'project-credit-budget',
    title: 'ProjectCreditBudget',
    about: 'Project spending and member task caps, with public spending shown separately.',
    file: 'src/components/settings/ProjectCreditBudget.vue',
    component: ProjectCreditBudget,
    needs: UI,
    states: [
      {
        name: 'Project administrator',
        note: 'Blank limits leave spending bounded by the team balance and project budget.',
        props: {
          editable: true,
          budget: {
            credits_limit: 1000,
            credits_used: 130,
            credits_remaining: 870,
            public_credits: 30,
            unattributed_credits: 0,
            members: { alice: { name: 'Alice', credits_limit: 200, credits_used: 100 } },
            since: '2026-10-01T00:00:00+00:00',
          },
        },
        expect: 'Alice',
      },
      {
        name: 'Project member',
        note: 'Members can read spending and limits; only project administrators can change them.',
        props: {
          editable: false,
          budget: {
            credits_limit: 1000,
            credits_used: 130,
            credits_remaining: 870,
            public_credits: 30,
            unattributed_credits: 0,
            members: { alice: { name: 'Alice', credits_limit: 200, credits_used: 100 } },
            since: '2026-10-01T00:00:00+00:00',
          },
        },
        expect: 'Alice',
      },
      {
        name: 'Read failed',
        note: 'A failed read keeps its error and a retry action.',
        props: { editable: false, budget: null, error: 'Budget information could not be loaded.' },
        expect: 'Budget information could not be loaded.',
      },
    ],
  },
  {
    id: 'settings-task-naming',
    title: 'ProjectTaskNamingSettings',
    about: '项目设置「任务命名」：两档单选，选中即保存；不能管理的人只看得见，不能点。',
    file: 'src/components/ProjectTaskNamingSettings.vue',
    component: ProjectTaskNamingSettings,
    needs: UI,
    states: [
      {
        name: '读到了，智能命名',
        note: '两档各一颗单选：智能命名亮着，说明「没起名的任务在第一条消息后自动起名」。',
        props: { state: taskNamingState(), error: '', busy: false },
        expect: '智能命名',
      },
      {
        name: '部署不支持自动起名',
        note: 'mode 还是智能命名，但 available 为假：底下补一句由 AI 队友在弄清任务后起名。',
        props: { state: taskNamingState({ available: false }), error: '', busy: false },
        expect: '当前部署无法自动起名',
      },
      {
        name: '读失败',
        note: '还没读到就是 null：那两档不画，只在上面留一句后端原话。',
        props: { state: null, error: '加载任务命名设置失败', busy: false },
        expect: '加载任务命名设置失败',
      },
    ],
  },
  {
    id: 'settings-compute',
    title: 'ProjectComputeSettings',
    about: '项目设置里的执行环境：新 AI 队友的默认环境与已开工队友的分布；修改默认不搬已有会话。',
    file: 'src/components/ProjectComputeSettings.vue',
    component: ProjectComputeSettings,
    needs: UI,
    states: [
      {
        name: '默认标准沙箱，已有会话分布',
        note: '默认环境是标准沙箱；分布按沙箱规格和每台设备计数，设备行保留会话入口。',
        props: {
          projectId: 'demo',
          deviceSessionsComponent: DeviceSessionsPreview,
          state: computeState(),
          error: '',
          busy: false,
          editing: true,
        },
        expect: '设备会话入口',
      },
      {
        name: '保存中',
        note: '一次保存还在路上：「更换」那颗按钮转圈、按不动。',
        props: { projectId: 'demo', state: computeState(), error: '', busy: true, editing: true },
        expectSelector: '.v-btn--loading',
      },
      {
        name: '读失败',
        note: '还没读到就是 null：只在上面留一句后端原话。',
        props: { projectId: 'demo', state: null, error: '加载环境设置失败', busy: false, editing: false },
        expect: '加载环境设置失败',
      },
    ],
  },
  {
    id: 'settings-default-model',
    title: 'ProjectDefaultModelSettings',
    about: '项目默认模型：主线（房间聊天）用哪一档、分身用哪一档；改完保存才生效，也能恢复部署默认。',
    file: 'src/components/ProjectDefaultModelSettings.vue',
    component: ProjectDefaultModelSettings,
    needs: UI,
    states: [
      {
        name: '跟着部署默认',
        note: '项目没显式设过：主模型那一栏显示部署兜底算出来的那档，「恢复默认模型」不出现（没有可恢复的）。两个下拉都在，保存按钮灰着（没有改动）。',
        props: { state: defaultModelState(), loadError: '', saving: false, saved: false, saveError: '' },
        expect: '项目主模型',
      },
      {
        name: '显式设过一档，可恢复',
        note: '项目显式设过默认分身模型：主模型那栏换成选中的那档，「恢复默认模型」出现。',
        props: {
          state: defaultModelState({ model: 'deepseek-flash', subagent_model: 'deepseek-flash' }),
          loadError: '',
          saving: false,
          saved: false,
          saveError: '',
        },
        expect: '恢复默认模型',
      },
      {
        name: '模型要更贵的方案',
        note: '选项里有一档当前方案不含：那一条标出「需要 Reserve 方案」，选了也不能保存。',
        props: {
          state: defaultModelState({
            deployment_default: 'deepseek-flash',
            choices: [
              modelChoice({ id: 'sonnet', label: 'Claude Sonnet 5', allowed: false, requires_plan: 'Reserve' }),
              modelChoice({ id: 'deepseek-flash', label: 'DeepSeek V4.1 Flash' }),
            ],
          }),
          loadError: '',
          saving: false,
          saved: false,
          saveError: '',
        },
        expect: '项目主模型',
      },
      {
        name: '保存中',
        note: '一次保存还在路上：两个下拉和保存按钮都灰掉，旁边那行回执写「保存中…」。',
        props: {
          state: defaultModelState({ model: 'deepseek-flash' }),
          loadError: '',
          saving: true,
          saved: false,
          saveError: '',
        },
        expect: '保存中…',
      },
      {
        name: '保存好了',
        note: '刚保存成功：旁边那行回执亮着「已保存」，过一会儿自己灭掉（这里停在亮着的那一刻）。',
        props: {
          state: defaultModelState({ model: 'deepseek-flash' }),
          loadError: '',
          saving: false,
          saved: true,
          saveError: '',
        },
        expect: '已保存',
      },
      {
        name: '读失败',
        note: '还没读到就是 null：两个下拉不画，只在上面留一句后端原话。',
        props: { state: null, loadError: '加载默认模型失败', saving: false, saved: false, saveError: '' },
        expect: '加载默认模型失败',
      },
    ],
  },
  {
    id: 'settings-environment',
    title: 'ProjectEnvironmentSettings',
    about: '项目设置里的运行环境：两段脚本、环境变量那几行、每个频道的准备现状，以及准备失败时「让芝士看看」。',
    file: 'src/components/ProjectEnvironmentSettings.vue',
    component: ProjectEnvironmentSettings,
    needs: UI,
    args: { agentName: '芝士' },
    states: [
      {
        name: '一个频道准备失败，芝士没看',
        note: '上面那张失败卡说清哪一步、有几段对话在等、日志末尾，并给「让芝士看看」和「重试」；脚本与环境变量在下面，可编辑。',
        props: {
          info: envInfo(),
          status: envStatus(),
          selectedRoom: 'r-general',
          error: '',
          saving: false,
          saved: false,
          saveError: '',
          applying: false,
          diagnosis: null,
          diagnosing: false,
        },
        expect: '让芝士看看',
      },
      {
        name: '芝士给了改法',
        note: '失败卡下面接上原因、改法，和逐行标出的脚本改动；「采用并重试」才保存并重试，不采用什么都变。',
        props: {
          info: envInfo(),
          status: envStatus(),
          selectedRoom: 'r-general',
          error: '',
          saving: false,
          saved: false,
          saveError: '',
          applying: false,
          diagnosis: envDiagnosis(),
          diagnosing: false,
        },
        expect: '采用并重试',
      },
      {
        name: '只读',
        note: '不能改的人：两段脚本和环境变量都是只读，「保存配置」「添加变量」都不出现。',
        props: {
          info: envInfo({ can_edit: false, rooms: [] }),
          status: null,
          selectedRoom: null,
          error: '',
          saving: false,
          saved: false,
          saveError: '',
          applying: false,
          diagnosis: null,
          diagnosing: false,
        },
        expect: '脚本与环境变量',
      },
      {
        name: '保存好了',
        note: '脚本与环境变量一次保存成功：那一行回执写「已保存。新频道使用这份配置，已有频道保持原配置」。',
        props: {
          info: envInfo(),
          status: envStatus({ state: 'ready', stage: undefined, exit_code: null }),
          selectedRoom: 'r-general',
          error: '',
          saving: false,
          saved: true,
          saveError: '',
          applying: false,
          diagnosis: null,
          diagnosing: false,
        },
        expect: '已保存。新频道使用这份配置，已有频道保持原配置',
      },
      {
        name: '保存失败',
        note: '保存被打回：那一行回执把原因原样接在后面。',
        props: {
          info: envInfo(),
          status: envStatus(),
          selectedRoom: 'r-general',
          error: '',
          saving: false,
          saved: false,
          saveError: '环境变量名称不能为空或重复',
          applying: false,
          diagnosis: null,
          diagnosing: false,
        },
        expect: '保存失败：环境变量名称不能为空或重复',
      },
      {
        name: '还在读',
        note: '第一态：读到之前只有一行转圈（脚本、环境变量、频道都还没画）。',
        props: {
          info: null,
          status: null,
          selectedRoom: null,
          error: '',
          saving: false,
          saved: false,
          saveError: '',
          applying: false,
          diagnosis: null,
          diagnosing: false,
        },
        expectSelector: '.v-progress-linear',
      },
      {
        name: '读失败',
        note: '读被打回：一句后端原话，加一颗「重新加载」。',
        props: {
          info: null,
          status: null,
          selectedRoom: null,
          error: '加载运行环境失败',
          saving: false,
          saved: false,
          saveError: '',
          applying: false,
          diagnosis: null,
          diagnosing: false,
        },
        expect: '重新加载',
      },
    ],
  },
  {
    id: 'settings-mcp',
    title: 'ProjectMcpSettings',
    about: '项目设置「MCP 服务器」：连接属于项目，谁授权的看得见，密钥只进不出。',
    file: 'src/components/ProjectMcpSettings.vue',
    component: ProjectMcpSettings,
    needs: UI_I18N,
    states: [
      {
        name: '两台服务器，一台要连接一台缺密钥',
        note: '远程的和在项目机器上运行的分两组；oauth 那台给「连接」，缺值的那台给「填写」，本地命令那台写着它的命令。',
        props: { list: mcpList(), error: '', busy: '', values: {}, manages: true, agentName: '芝士' },
        expect: '在项目机器上运行',
      },
      {
        name: '连上了，写着谁授权',
        note: '连上之后那一行写「已连接（由 @alice 授权）」并给「断开」；任何成员都能断开。',
        props: {
          list: mcpList({
            servers: [
              { ...mcpServer(), status: 'connected', authorized_by: 'alice', authorized_at: '2026-10-11T09:00:00Z' },
            ],
          }),
          error: '',
          busy: '',
          values: {},
          manages: true,
          agentName: '芝士',
        },
        expect: '已连接',
      },
      {
        name: '缺值的那台收着填写框',
        note: '填值默认收起：缺值时按钮是「填写」，点开才出现 password 框。',
        props: {
          list: mcpList(),
          error: '',
          busy: '',
          values: { SEARCH_KEY: 'sk-123' },
          manages: true,
          agentName: '芝士',
        },
        expect: '填写',
      },
      {
        name: '一次动作在路上',
        note: '某一台正在连：那一行那颗按钮转圈（busy 是那台的名字）。',
        props: { list: mcpList(), error: '', busy: 'tracker', values: {}, manages: true, agentName: '芝士' },
        expectSelector: '.v-btn--loading',
      },
      {
        name: '清单读不出来',
        note: '仓库里的 .mcp.json 格式不对：不画清单，只留一句说明。',
        props: {
          list: mcpList({ servers: [], problem: 'invalid' }),
          error: '',
          busy: '',
          values: {},
          manages: true,
          agentName: '芝士',
        },
        expect: '无法读取 .mcp.json：格式不正确',
      },
      {
        name: '读失败',
        note: '还没读到就是 null：只在上面留一句后端原话。',
        props: { list: null, error: '读取 MCP 服务器失败', busy: '', values: {}, manages: true, agentName: '芝士' },
        expect: '读取 MCP 服务器失败',
      },
      {
        name: '一条本地命令启动失败',
        note: '状态写「启动失败」，日志收在「查看日志」后面，点开看最后几行。',
        props: {
          list: mcpList({
            servers: [mcpLocal({ run: { state: 'failed', log: ['ModuleNotFoundError: notes'], owner: null } })],
          }),
          error: '',
          busy: '',
          values: {},
          manages: true,
          agentName: '芝士',
        },
        expect: '查看日志',
      },
      {
        name: '等团队管理员允许',
        note: '项目机器是团队设备：命令等团队管理员在「等你处理」里允许。',
        props: {
          list: mcpList({ servers: [mcpLocal({ run: { state: 'awaiting', log: [], owner: 'dana' } })] }),
          error: '',
          busy: '',
          values: {},
          manages: true,
          agentName: '芝士',
        },
        expect: '允许',
      },
    ],
  },
  {
    id: 'project-role-list',
    title: 'ProjectRoleList',
    about: '项目设置里「角色和权限」的角色清单：管理员、成员在上，项目自己的角色在一道缝下面，每行写着几个人。',
    file: 'src/components/settings/ProjectRoleList.vue',
    component: ProjectRoleList,
    needs: ['vuetify', 'i18n'],
    states: [
      {
        name: '有自己的角色',
        note: '项目自己的角色排在一道缝下面；每行的人数是拿着这个角色的人，所有者不算。',
        props: { roles: DEMO_ROLES },
        expect: '内测用户',
      },
      {
        name: '只有内置的两个',
        note: '没有自己的角色时不画那道缝。',
        props: { roles: DEMO_ROLES.slice(0, 2) },
        expect: '成员',
      },
    ],
  },
  {
    id: 'project-role-detail',
    title: 'ProjectRoleDetail',
    about: '一个角色自己那一页：按组排的权限开关，管人和改角色两项锁定。',
    file: 'src/components/settings/ProjectRoleDetail.vue',
    component: ProjectRoleDetail,
    needs: ['vuetify', 'i18n'],
    states: [
      {
        name: '管理员看到的',
        note: '能拨；管人和改角色两项带锁、拨不动。',
        props: { role: DEMO_ROLES[2], editable: true, agentName: '芝士' },
        expect: '修改项目设置',
      },
      {
        name: '只能看',
        note: '不能修改角色的人看到的：每个开关都是灰的。',
        props: { role: DEMO_ROLES[1], editable: false, agentName: '芝士' },
        expect: '在频道发言',
      },
    ],
  },
  {
    id: 'role-create-dialog',
    title: 'RoleCreateDialog',
    about: '新增角色：起名字，权限从现有角色复制；项目还没有导师角色时，可以从导师模板开始。',
    file: 'src/components/settings/RoleCreateDialog.vue',
    component: RoleCreateDialog,
    needs: ['vuetify', 'i18n'],
    teleport: true,
    args: { modelValue: true, roles: DEMO_ROLES, saving: false, error: null },
    states: [
      {
        name: '打开',
        note: '名字没起时「新增」是灰的；从导师模板开始可以不起名。',
        props: {},
        expect: '权限复制自',
      },
    ],
  },
  {
    id: 'role-delete-dialog',
    title: 'RoleDeleteDialog',
    about: '删除还有人在用的角色：先选他们改为什么角色。',
    file: 'src/components/settings/RoleDeleteDialog.vue',
    component: RoleDeleteDialog,
    needs: ['vuetify', 'i18n'],
    teleport: true,
    args: { modelValue: true, role: DEMO_ROLES[2], roles: DEMO_ROLES, makesAdmins: true, saving: false, error: null },
    states: [
      {
        name: '还有 12 人',
        note: '先选这些人改为什么角色，选的角色随删除一起交出去。',
        props: {},
        expect: '这个角色的 12 人改为',
      },
    ],
  },
  {
    id: 'move-project-dialog',
    title: 'MoveProjectDialog',
    about: '成员页右上角「移到团队」打开的对话框：所有者把个人项目移到自己管理的团队。',
    file: 'src/components/MoveProjectDialog.vue',
    component: MoveProjectDialog,
    needs: ['vuetify', 'i18n'],
    teleport: true,
    states: [
      {
        name: '有可选的团队',
        note: '挑一个团队，「移到这个团队」才按得下去。',
        props: {
          modelValue: true,
          teams: [
            { id: 1, name: '视觉课题组' },
            { id: 2, name: '机器人社' },
          ],
          loading: false,
          moving: false,
          error: null,
        },
        expect: '项目会归到所选团队',
      },
      {
        name: '没有你管理的团队',
        note: '说清楚，不给选择框。',
        props: { modelValue: true, teams: [], loading: false, moving: false, error: null },
        expect: '暂无你管理的团队',
      },
    ],
  },
]
