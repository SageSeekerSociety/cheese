/**
 * 项目设置页（`views/ProjectSettingsView.vue`）拆出来的六件在预览站里的条目。
 *
 * 它们和别的条目没有两样（「这是什么 / 在哪儿 / 需要哪几样 / 看哪几格」，规矩见
 * `catalog.ts`），单独一份是因为 `catalog.ts` 已经八百多行，六条塞进去会顶到
 * `frontend/src` 那一千行的上限 —— 和 `catalogQueue.ts`、`catalogAccept.ts` 同一个理由
 * （#2143 的几个拆分 PR 都是这么做的）。数据见 `catalogSettingsFixtures.ts`。
 *
 * 为什么这六件值得一站：拆之前它们是 1041 行 `ProjectSettingsView.vue` 里的六段模板，
 * 想看其中任何一段都得把整页拉起来 —— 而那一页自己去接口取数（分支保护还要问 GitHub）、
 * 还要等所有请求到齐才掀开那层转圈。拆开之后每一件都只吃 props、只往上发事件
 * （`frontend_grade.py` 的 A 级，取数全在 `composables/useProjectSettings.ts` 和
 * `composables/useBranchProtection.ts` 里），于是每一件都能单独摆在预览站里。
 *
 * 这里没有登记整页：整页现在只剩接线（取数 + 把六件摆进四组里），而且它自己不是
 * A 级，在场景棘轮眼里是那棵树上的欠债 —— 「场景」指的是路由页和
 * `components/panels/**`，`components/settings/**` 这几件不是场景。
 *
 * 末尾另收「归档项目」那一块（`ArchiveProjectSection`）和它的确认框（`ArchiveProjectDialog`）：
 * 两件都是哑的，请求和状态在 `composables/useProjectArchive.ts`。
 *
 * 再收 `EnvironmentPicker`（选执行环境的那几组单选，设置页的算力那一组和名册都用它）
 * 和包着它的 `EnvironmentMenu`（名册行尾那颗「改」），从 `catalog.ts` 搬来：那份文件
 * 到了一千行的上限。
 *
 * 这里的 `CatalogEntry` 是 type-only 引用：`catalog.ts` 反过来要 `SETTINGS_ENTRIES` 这个
 * 值，运行时不构成循环。
 */
import type { CatalogEntry, CatalogNeed } from './catalog'

import { COMPUTE_DEVICES } from './catalogFixtures'
import {
  ARCHIVE_PROJECT,
  ARCHIVE_REFUSED,
  bpProps,
  bpRules,
  callbackNotice,
  forgeConn,
} from './catalogSettingsFixtures'

import ArchiveProjectDialog from '@/components/ArchiveProjectDialog.vue'
import EnvironmentMenu from '@/components/EnvironmentMenu.vue'
import EnvironmentPicker from '@/components/EnvironmentPicker.vue'
import McpServerDialog from '@/components/McpServerDialog.vue'
import ArchiveProjectSection from '@/components/settings/ArchiveProjectSection.vue'
import AttributionSettings from '@/components/settings/AttributionSettings.vue'
import BranchProtectionSection from '@/components/settings/BranchProtectionSection.vue'
import ForgeRepoStatus from '@/components/settings/ForgeRepoStatus.vue'
import GithubRepoSettings from '@/components/settings/GithubRepoSettings.vue'
import UpstreamRepoSettings from '@/components/settings/UpstreamRepoSettings.vue'

/** 这几件都吃 vuetify；读当前 locale 的（`relTime` 之类）再多一样 i18n。 */
const UI: CatalogNeed[] = ['vuetify']
const UI_I18N: CatalogNeed[] = ['vuetify', 'i18n']

export const SETTINGS_ENTRIES: CatalogEntry[] = [
  {
    id: 'settings-branch-protection',
    title: 'BranchProtectionSection',
    about:
      '交付那一组的分支保护：平台侧的合并规则，照 GitHub 分支保护那一页的顺序排；GitHub 自己开了保护的规则灰掉而不是藏起来。',
    file: 'src/components/settings/BranchProtectionSection.vue',
    component: BranchProtectionSection,
    needs: UI,
    states: [
      {
        name: '读到了，GitHub 没开保护',
        note: '九条规则按「检查 → 跟上 main → 作废采纳 → 自动合并 → 放行名单 → 批准人数 → 合并方式 → 默认 reviewer」排；填了路径范围的检查只在改到对应文件时要求。',
        props: bpProps({
          bp: bpRules({
            required_checks: [{ name: 'ci / tests', paths: ['backend/**', 'frontend/**'] }, { name: 'lint' }],
            approvals_required: 2,
            override_handles: ['alice'],
            default_reviewer: 'wang',
          }),
          checkName: '网页端 e2e',
          checkPaths: 'frontend/**',
          approvalsDraft: '2',
        }),
        expect: '合并前必须通过的检查',
      },
      {
        name: 'GitHub 已在执行',
        note: '顶行说清这是 GitHub 在管，同名规则（必须通过的检查、跟上 main、作废采纳、放行名单、批准人数）灰掉；灰掉不是藏起来 —— 值还看得见，只是不能在这儿改。',
        props: bpProps({ bp: bpRules({ github_protection: { enforced: true, status: 'enforced' } }) }),
        expect: 'GitHub 已在执行以下规则',
      },
      {
        name: '查不到 GitHub 的状态',
        note: '查不到 ≠ 已开启：不灰任何一条，只加一行淡色说明 —— 灰掉会让「其实能改」的规则看起来不能改。',
        props: bpProps({ bp: bpRules({ github_protection: { enforced: false, status: 'unknown' } }) }),
        expect: '无法读取 GitHub 上的保护状态',
      },
      {
        name: '正在保存一条规则',
        note: '保存中所有控件一起禁用，转圈只转在改的那一条上（`saving` 是那条规则的 key）；上一次保存失败那句话挂在顶上那颗可关掉的 alert 里。',
        props: bpProps({
          bp: bpRules({ strict: false }),
          saving: 'strict',
          error: '保存失败：请求超时',
        }),
        expect: '保存失败：请求超时',
      },
      {
        name: '读失败',
        note: '这一块的读取要问 GitHub，比这一页别的请求慢，也可能单独失败；失败时给原话和一颗「重试」，不是画成一份空规则。',
        props: bpProps({ state: 'error', loadError: '请求超时' }),
        expect: '重试',
      },
    ],
  },
  {
    id: 'settings-upstream-repo',
    title: 'UpstreamRepoSettings',
    about:
      '上游仓库地址：接一个已有的 GitHub 仓库、把它的历史拉进来。只在还没接上仓库的 GitHub 项目里出现（那个条件在页面手里）。',
    file: 'src/components/settings/UpstreamRepoSettings.vue',
    component: UpstreamRepoSettings,
    needs: UI,
    states: [
      {
        name: '还没填',
        note: '一段地址、一颗「保存」。地址本身不是可点的东西：要不要去这个仓库，是保存之后「连接 GitHub 仓库」那一步的事。',
        props: { url: '', saving: false },
        expect: 'GitHub 仓库地址',
      },
      {
        name: '保存中',
        note: '保存那一颗转圈并禁用，输入框里留着人刚才填的原文；保存成功之后这里会被换成后端回话的规范化地址。',
        props: { url: 'https://github.com/acme/code', saving: true },
        expect: '保存',
      },
    ],
  },
  {
    id: 'settings-forge-repo',
    title: 'ForgeRepoStatus',
    about: '由平台托管的项目（forgejo）的代码仓库那一块：一行状态。不给去 Forgejo 的链接：人在那边没有账号。',
    file: 'src/components/settings/ForgeRepoStatus.vue',
    component: ForgeRepoStatus,
    needs: UI,
    states: [
      {
        name: '已经托管好了',
        note: '托管服务是建项目时定下来的，所以这里没有可编的东西 —— 这一件只画当前状态，连一个 emit 都没有。',
        props: {
          forge: forgeConn({
            kind: 'forgejo',
            connected: true,
            repo: 'acme/code',
            url: 'https://git.example/acme/code',
          }),
        },
        expect: '由平台托管',
      },
      {
        name: '仓库还在准备',
        note: '刚建的项目：仓库还没备好。',
        props: { forge: forgeConn({ kind: 'forgejo', connected: false }) },
        expect: '仓库准备中',
      },
    ],
  },
  {
    id: 'settings-github-repo',
    title: 'GithubRepoSettings',
    about: '连接 GitHub 仓库：cheesex-app 装到这个仓库上，之后这个项目的 git 操作走这个 installation 的短时 token。',
    file: 'src/components/settings/GithubRepoSettings.vue',
    component: GithubRepoSettings,
    needs: UI,
    states: [
      {
        name: '还没接',
        note: '没接上时给的是主动作（实心的「连接 GitHub 仓库」），因为这一格的下一步只有一件事可做。',
        props: { forge: forgeConn(), connecting: false, notice: null },
        expect: '暂无关联仓库',
      },
      {
        name: '接上了',
        note: '接上之后按钮换成「重新连接」：重连是修（token 掉了、仓库换了），不是这儿的日常动作。',
        props: { forge: forgeConn({ connected: true, repo: 'acme/code' }), connecting: false, notice: null },
        expect: '已连接',
      },
      {
        name: '接失败了',
        note: '结果只报在自己这一块：失败原因是一句能照着做的事（`@/lib/githubAccount` 那张表），不是后端给的 reason 码 ——「连接仓库失败：already_linked」这种事发生过一次（#222）。',
        props: {
          forge: forgeConn(),
          connecting: false,
          notice: callbackNotice({
            type: 'error',
            text: '连接 GitHub 仓库失败：这次安装没有授权任何仓库。在 GitHub 的安装页里至少勾选一个仓库',
          }),
        },
        expect: '这次安装没有授权任何仓库',
      },
    ],
  },
  {
    id: 'settings-attribution',
    title: 'AttributionSettings',
    about:
      '提交署名：项目的 PR 和提交算任务负责人的，还是算 AI 队友的。选中哪种、存没存上由页面管，这一件只画、只把选中的那一种报上去。',
    file: 'src/components/settings/AttributionSettings.vue',
    component: AttributionSettings,
    needs: UI,
    states: [
      {
        name: '署名给任务负责人',
        note: '默认的那一种。下面那句说清这一种落下去之后 PR 由谁开、谁是作者，以及负责人没连接 GitHub 时会怎样。',
        props: {
          mode: 'owner',
          agentName: '芝士',
          saving: false,
          error: null,
          github: { state: 'linked', login: 'HuanCheng65' },
          manageTo: '/users/settings/security',
        },
        expect: '用任务负责人连接的 GitHub 账号创建 PR',
      },
      {
        name: '还没连接 GitHub',
        note: '署名给任务负责人时，下面那一行说看的人自己的账号：还没连接就给一个就地连接的入口。',
        props: {
          mode: 'owner',
          agentName: '芝士',
          saving: false,
          error: null,
          github: { state: 'none' },
          manageTo: '/users/settings/security',
        },
        expect: '连接后，你负责的任务用你的账号创建 PR',
      },
      {
        name: 'GitHub 授权失效',
        note: '没有能续期的 token 才算失效：这时他负责的任务改由 AI 队友创建 PR，这一行提示重新授权 GitHub。',
        props: {
          mode: 'owner',
          agentName: '芝士',
          saving: false,
          error: null,
          github: { state: 'expired', login: 'HuanCheng65' },
          manageTo: '/users/settings/security',
        },
        expect: '授权已失效',
      },
      {
        name: '署名给 AI 队友',
        note: '选项叫「AI 队友」，和频道里改设置那一行说的一样；说明里用项目给 AI 队友起的名字，不写死。',
        props: {
          mode: 'agent',
          agentName: '芝士',
          saving: false,
          error: null,
          github: { state: 'linked', login: 'HuanCheng65' },
          manageTo: '/users/settings/security',
        },
        expect: 'PR 和提交都署 芝士 的名',
      },
      {
        name: '保存失败',
        note: '存不上时说清是保存失败，不把选择悄悄吞掉（下拉里画的还是刚选的那一种，因为那是人刚点过的）。',
        props: {
          mode: 'agent',
          agentName: '芝士',
          saving: false,
          error: '保存失败，稍后重试',
          github: { state: 'unknown' },
          manageTo: '/users/settings/security',
        },
        expect: '保存失败，稍后重试',
      },
    ],
  },
  {
    id: 'environment-menu',
    title: 'EnvironmentMenu',
    about:
      '名册里一条会话、或「新会话默认」那一行行尾的「改」：点开是环境选择，选定后交给 `save`，提议和错误留在菜单里。',
    file: 'src/components/EnvironmentMenu.vue',
    component: EnvironmentMenu,
    needs: UI,
    states: [
      {
        name: '收着',
        note: '只有行尾那颗「改」；点开才是环境选择。',
        props: {
          heading: '芝士 的环境',
          current: { kind: 'sandbox', spec: 'standard' },
          devices: COMPUTE_DEVICES,
          specs: [
            { spec: 'standard', label: '标准' },
            { spec: 'vm', label: '虚拟机' },
          ],
          save: async () => {},
        },
        expect: '改',
      },
    ],
  },
  {
    id: 'environment-picker',
    title: 'EnvironmentPicker',
    about:
      '选一个执行环境：沙箱（选规格：标准、虚拟机、团队设备），或者指定一台设备。沙箱在哪台机器上不显示；只有设备所在团队的管理员能指定设备。',
    file: 'src/components/EnvironmentPicker.vue',
    component: EnvironmentPicker,
    needs: UI,
    states: [
      {
        name: '标准沙箱',
        note: '默认选中沙箱 · 标准；每种规格旁边是接口给的说明和每小时价格（这里的价格是示例）；团队管理员可以指定两台团队设备，其中一台离线。',
        props: {
          current: { kind: 'sandbox', spec: 'standard' },
          devices: COMPUTE_DEVICES.map((device) => ({ ...device, owned: true })),
          specs: [
            {
              spec: 'standard',
              label: '标准沙箱',
              description:
                '每位 AI 队友一个沙箱，在平台的机器上，只看得到自己的工作目录；按运行时长计费，每小时 5 点。',
              i18n: { description: { key: 'specStandardAbout', params: { credits: 5 } } },
            },
            {
              spec: 'vm',
              label: '虚拟机沙箱',
              description: '一整台虚拟机给一位 AI 队友：能跑 Docker、KVM、装内核模块；4 核 8 GiB，每小时 20 点。',
              i18n: { description: { key: 'specVmAbout', params: { cores: 4, memory: 8, credits: 20 } } },
            },
            { spec: 'team_devices', label: '团队设备' },
          ],
        },
        expect: '只看得到自己的工作目录和本项目的包缓存',
      },
      {
        name: '普通成员',
        note: '当前队友已在指定设备上运行；普通成员可以查看，不能指定整台设备。',
        props: {
          current: { kind: 'self_hosted', device_id: COMPUTE_DEVICES[0].device_id },
          devices: COMPUTE_DEVICES.map((device) => ({ ...device, owned: false })),
          specs: [
            { spec: 'standard', label: '标准' },
            { spec: 'vm', label: '虚拟机' },
            { spec: 'team_devices', label: '团队设备' },
          ],
        },
        expect: '只有设备所在团队的管理员能指定',
      },
    ],
  },
  {
    id: 'settings-archive-project',
    title: 'ArchiveProjectSection',
    about: '项目设置最后一块：归档。一句说明、一颗按钮，点了打开把项目名打一遍的确认框。只有所有者看得到。',
    file: 'src/components/settings/ArchiveProjectSection.vue',
    component: ArchiveProjectSection,
    needs: UI,
    states: [
      {
        name: '所有者看到的那一行',
        note: '说明和按钮一行，放不下时按钮换到下一行。确认框收着：开没开是这一块自己的事，props 只往它里面递「正在归档 / 被拒的理由」，所以这一块只有这一个样子。',
        props: { projectName: ARCHIVE_PROJECT },
        expect: '从所有成员的列表中移除并停止运行，内容全部保留',
      },
    ],
  },
  {
    id: 'settings-archive-project-dialog',
    title: 'ArchiveProjectDialog',
    about: '「归档项目」的确认框：把项目名打一遍才放行；被拒不关窗，理由原样留在框里，重开时清掉。',
    file: 'src/components/ArchiveProjectDialog.vue',
    component: ArchiveProjectDialog,
    needs: UI,
    teleport: true,
    args: { modelValue: true, projectName: ARCHIVE_PROJECT },
    states: [
      {
        name: '刚打开，还没输入',
        note: '「归档」是红色的危险主操作，名字没打对之前是灰的。输入框里打的字是框自己的状态，不从 props 来，所以「打对了名字」那一格要在框里自己打一遍。',
        props: {},
        expectSelector: '.v-btn--disabled',
      },
      {
        name: '正在归档',
        note: 'archiving 时主按钮转圈；一次归档结束（archiving 落回 false）而没有理由，框自己关上。',
        props: { archiving: true },
        expectSelector: '.v-btn--loading',
      },
      {
        name: '被拒，带着理由',
        note: '后端那句理由原样画在框里，框不关，可以改了再试。',
        props: { error: ARCHIVE_REFUSED },
        expect: ARCHIVE_REFUSED,
      },
    ],
  },
  {
    id: 'settings-mcp-server-dialog',
    title: 'McpServerDialog',
    about:
      '「添加 MCP 服务器」/「编辑」那张表：远程服务器（名称、地址、登录方式）或在项目机器上运行的命令（名称、命令、环境变量）。',
    file: 'src/components/McpServerDialog.vue',
    component: McpServerDialog,
    needs: UI_I18N,
    teleport: true,
    args: { modelValue: true, initial: null, agentName: '芝士', saving: false, error: '' },
    states: [
      {
        name: '添加',
        note: '名称、地址都填对之前「添加」是灰的；默认用账号登录。',
        props: {},
        expectSelector: '.v-btn--disabled',
      },
      {
        name: '编辑一条填请求头的',
        note: '名称不能改；请求头的值不回显，留空就是不改。',
        props: {
          initial: {
            kind: 'remote',
            name: 'sentry',
            url: 'https://mcp.sentry.dev/mcp',
            auth: 'headers',
            header: 'Authorization',
            value: null,
          },
        },
        expect: '编辑 sentry',
      },
      {
        name: '编辑一条命令',
        note: '类型不能改；命令和环境变量照原样回显，密钥写成变量。',
        props: {
          initial: {
            kind: 'command',
            name: 'postgres',
            command: 'npx -y @modelcontextprotocol/server-postgres ${DATABASE_URL}',
            env: { PGSSLMODE: 'require' },
          },
        },
        expect: '编辑 postgres',
      },
      {
        name: '保存被拒',
        note: '服务端的原话留在框里，框不关。',
        props: { error: '地址必须是 https' },
        expect: '地址必须是 https',
      },
    ],
  },
]
