/**
 * 工作面板那一组（`components/panels/*.vue`）在预览站里的条目。
 *
 * 规矩和别的条目一样（「这是什么 / 在哪儿 / 需要哪几样 / 看哪几格」，见 `catalog.ts`）。
 * 单独一份是因为 `catalog.ts` 已经九百多行，十一条塞进去会顶到 `frontend/src` 那一千
 * 行的上限。`CatalogEntry` 是 type-only 引用：`catalog.ts` 反过来要 `PANEL_ENTRIES`
 * 这个值，运行时不构成循环。
 *
 * 三只壳子（`PanelChanges` / `PanelPreview` / `PanelDoc`）和 `catalog.ts` 里那三只
 * View（`panel-changes` / `panel-preview` / `panel-doc`）不是重复：View 那几条看的是
 * 「这一串 props 画成什么」，这里看的是「取数那一层的整包递进来，壳子摊开以后接得上」。
 * 包里有哪几样、各是 ref 还是 computed，由 `catalogPanelsFixtures.ts` 里那几包的返回
 * 类型（composable 的 `ReturnType`）守着 —— 少一样是 typecheck 报错；这几格挂起来
 * 看的是壳子摊开递下去以后，View 画出了这一格该有的那句话。
 */
import type { CatalogEntry, CatalogNeed } from './catalog'

import { DOC_TOPIC, docSession, NO_REPO, NOTHING_CHANGED } from './catalogFixtures'
import { DOC_PANEL_ENTRIES } from './catalogPanelsDoc'
import {
  BUILD_LOG_BYTES,
  CHANGES_ALL_PROPS,
  CHANGES_BAR_PROPS,
  CHANGES_CONTENT_PROPS,
  CHANGES_TASK,
  changesBundle,
  checklistAt,
  diffLinesOf,
  docBundle,
  docPeopleBundle,
  docThreadsBundle,
  FILE_DIFFS,
  fileTreeRows,
  loadBuildTail,
  loadLsOutput,
  LONG_DIFF_LINES,
  LS_OUTPUT_BYTES,
  PANEL_NAMES,
  previewBundle,
  projectFileProps,
  ROUTINE_ACCEPTED,
  ROUTINE_RUNS,
  ROUTINE_WEEKLY,
  SITE_ACT_BASH_OUTPUT,
  SITE_ACT_EDIT,
  SITE_ACT_FAILED,
  SITE_ACT_PLATFORM,
  SITE_AGENT_LABELS,
  SITE_AGENTS,
  SITE_DONE,
  SITE_ONE_TURN,
  SITE_SAY_DETAIL,
  SITE_SAY_SHORT,
  SITE_TURN_START,
  SITE_WORKING,
  siteBundle,
  THREAD_ROWS,
  threadTime,
  WORKSPACE_FILES,
} from './catalogPanelsFixtures'
import { PREVIEW_PANEL_ENTRIES } from './catalogPanelsPreview'
import { DEMO_PROJECT, DEMO_TOPIC } from './demoPanels'

import ChangesAllView from '@/components/panels/ChangesAllView.vue'
import ChangesDiff from '@/components/panels/ChangesDiff.vue'
import ChangesDiffList from '@/components/panels/ChangesDiffList.vue'
import ChangesFileBar from '@/components/panels/ChangesFileBar.vue'
import ChangesFileContent from '@/components/panels/ChangesFileContent.vue'
import ChangesFileTree from '@/components/panels/ChangesFileTree.vue'
import ChangesMoreMenu from '@/components/panels/ChangesMoreMenu.vue'
import ChangesOpenFile from '@/components/panels/ChangesOpenFile.vue'
import PanelChanges from '@/components/panels/PanelChanges.vue'
import PanelDoc from '@/components/panels/PanelDoc.vue'
import PanelPreview from '@/components/panels/PanelPreview.vue'
import PanelRoutines from '@/components/panels/PanelRoutines.vue'
import PanelSite from '@/components/panels/PanelSite.vue'
import PanelThreads from '@/components/panels/PanelThreads.vue'
import ProjectFileView from '@/components/panels/ProjectFileView.vue'
import SiteActRow from '@/components/panels/SiteActRow.vue'
import SiteAgentTabs from '@/components/panels/SiteAgentTabs.vue'
import SiteSayRow from '@/components/panels/SiteSayRow.vue'
import SiteStepOutput from '@/components/panels/SiteStepOutput.vue'
import SiteTurnHead from '@/components/panels/SiteTurnHead.vue'
import TodoChecklist from '@/components/panels/TodoChecklist.vue'

const UI: CatalogNeed[] = ['vuetify']

export const PANEL_ENTRIES: CatalogEntry[] = [
  {
    id: 'changes-diff-list',
    title: 'ChangesDiffList',
    about: '「改动」默认那一面：这次改到的每个文件一段，差异连着往下排，段头写路径和增删，可以单独收起或打开。',
    file: 'src/components/panels/ChangesDiffList.vue',
    component: ChangesDiffList,
    needs: ['vuetify', 'i18n'],
    states: [
      {
        name: '三个文件',
        note: '新增、改过、删掉的各一份；git 的头信息不画，@@ 换成行号区间。删掉的文件没有「打开」。',
        props: { diffs: FILE_DIFFS },
        expect: '这个项目放本课程的课件和作业。',
      },
      {
        name: '什么都没改',
        note: '空的时候说「暂无改动」。',
        props: { diffs: [] },
        expect: '暂无改动',
      },
    ],
  },
  {
    id: 'changes-more-menu',
    title: 'ChangesMoreMenu',
    about:
      '「改动」的 ⋯：看哪个版本、下载打开着的这份、刷新。文件清单那一行、滚动后的细栏、单独打开一份时的横条上都是它。',
    file: 'src/components/panels/ChangesMoreMenu.vue',
    component: ChangesMoreMenu,
    needs: ['vuetify', 'i18n'],
    states: [
      {
        name: '收着',
        note: '只有一颗 ⋯：版本和刷新偶尔才用，不占横条。',
        props: { currentTask: CHANGES_TASK, fileSource: 'committed', canDownload: true, refreshing: false },
        expectSelector: '[aria-label="更多"]',
      },
    ],
  },
  {
    id: 'changes-open-file',
    title: 'ChangesOpenFile',
    about: '「打开其他文件」：在这个任务的版本里按名字找一份文件打开，树上只列改到的那些。',
    file: 'src/components/panels/ChangesOpenFile.vue',
    component: ChangesOpenFile,
    needs: ['vuetify', 'i18n'],
    teleport: true,
    states: [
      {
        name: '刚打开',
        note: '没输入时按路径列出来，最多五十个；输入几段字，每段都要出现在路径里，文件名里就有的排前面。',
        props: { modelValue: true, files: WORKSPACE_FILES },
        expect: 'syllabus.md',
      },
    ],
  },
  {
    id: 'changes-diff',
    title: 'ChangesDiff',
    about: '改动那一格右半边的逐行 diff：行号两列、增删着色、长行折行，太长的一份先铺前 400 行。',
    file: 'src/components/panels/ChangesDiff.vue',
    component: ChangesDiff,
    needs: [],
    states: [
      {
        name: '一份新文件',
        note: '整份都是新增：旧行号那一列空着，新行号从 1 数起，每一行都是增的底色。',
        props: { lines: diffLinesOf('README.md') },
        expect: '这个项目放本课程的课件和作业。',
      },
      {
        name: '改过的文件',
        note: '增删交错的一段：删的那一行只有旧行号，加的只有新行号，没动的两列都有。',
        props: { lines: diffLinesOf('docs/week-1.md') },
        expect: '习题答案：第 3、5、7、9 题',
      },
      {
        name: '删掉的文件',
        note: '整份都是删除：只剩旧行号那一列。',
        props: { lines: diffLinesOf('docs/old-plan.md') },
        expect: '这一版已经不用了。',
      },
      {
        name: '很长的一份',
        note: '超过 400 行先收着，底下一颗按钮报还剩多少行；验收要看的是改了什么，不是一口气读完几百行。',
        props: { lines: LONG_DIFF_LINES },
        expect: '显示剩余',
      },
    ],
  },
  {
    id: 'changes-file-tree',
    title: 'ChangesFileTree',
    about: '改动那一格左边的文件树：文件夹可开合，每份文件上标着这一支活对它的增删。',
    file: 'src/components/panels/ChangesFileTree.vue',
    component: ChangesFileTree,
    needs: UI,
    args: {
      collapsedDirs: new Set<string>(),
      activePath: null,
      revealTick: 0,
      emptyLabel: '暂无改动',
    },
    states: [
      {
        name: '这一支改到的文件',
        note: '一份清单，文件夹默认摊开：新增、删除写成字，改过的给 +N −M；打开着的那一份高亮。',
        props: { rows: fileTreeRows(), activePath: 'README.md' },
        expect: '新增',
      },
      {
        name: '收起一个文件夹',
        note: '文件多的时候可以把读完的文件夹收起来，它的子树不再占行。',
        props: { rows: fileTreeRows(['docs']), collapsedDirs: new Set(['docs']) },
        expectSelector: '.file-item--dir[title="docs"] .mdi-folder-outline',
      },
      {
        name: '什么都没改',
        note: '空的时候说哪句话由外面给：看改动说「暂无改动」，看全部文件说「暂无文件」。',
        props: { rows: [] },
        expect: '暂无改动',
      },
    ],
  },
  {
    id: 'changes-file-bar',
    title: 'ChangesFileBar',
    about: '改动那一格打开一份文件时顶上那条横条：返回、这份文件、它的两面（差异 / 全文），以及能对它做的事。',
    file: 'src/components/panels/ChangesFileBar.vue',
    component: ChangesFileBar,
    needs: UI,
    args: {},
    states: [
      {
        name: '打开着改过的一份',
        note: '一头写着这件任务的状态，中间是路径和两面对切的开关，另一头是下载和版本菜单；没打开文件时整条不画。',
        props: CHANGES_BAR_PROPS,
        expect: '差异',
      },
      {
        name: '改到一半',
        note: '有没保存的改动时路径后面点一颗点，切到全文面就摆出保存；没改动之前那颗按钮是灰的。',
        props: { ...CHANGES_BAR_PROPS, effectiveView: 'edit' as const, fileDirty: true },
        expect: '保存',
      },
      {
        name: '打不开的一份',
        note: '二进制、图片、过大的文件没有编辑面，也就没有保存：这里摆一句「只读」，省得人以为是自己没权限。',
        props: { ...CHANGES_BAR_PROPS, fileReadOnly: true },
        expectSelector: '.changes-bar__ro',
      },
    ],
  },
  {
    id: 'changes-file-content',
    title: 'ChangesFileContent',
    about: '改动那一格里打开着的那一份的正文：文档画成一页，文本按差异或全文出，图片、二进制、过大的各给一个说法。',
    file: 'src/components/panels/ChangesFileContent.vue',
    component: ChangesFileContent,
    needs: UI,
    args: {},
    states: [
      {
        name: '改过的那一份',
        note: '默认看差异：新增的行是新建的 README，一整个文件都算增。',
        props: CHANGES_CONTENT_PROPS,
        expect: '这个项目放本课程的课件和作业',
      },
      {
        name: '一张图',
        note: '图片没有文本差异可看，也不该拿去按文本编辑：直接把原图摆出来。',
        props: { ...CHANGES_CONTENT_PROPS, effectiveView: 'edit' as const, openIsImage: true },
        expectSelector: '.file-image-view img',
      },
      {
        name: '二进制文件',
        note: '解不出文本的文件说清是二进制，只给下载原文件这一条路。',
        props: { ...CHANGES_CONTENT_PROPS, effectiveView: 'edit' as const, fileBinary: true, fileBytes: 40960 },
        expect: '非文本文件，无法编辑',
      },
      {
        name: '文件太大',
        note: '超过上限的一份不往浏览器里搬，报一下有多大、让人下载下去看。',
        props: { ...CHANGES_CONTENT_PROPS, effectiveView: 'edit' as const, fileTooLarge: true, fileBytes: 9437184 },
        expect: '文件过大，无法在浏览器中打开',
      },
      {
        name: '文档预览没开',
        note: '这台机器没装文档渲染器时，不摆一页空白：说明这里看不了，下载原文件看。',
        props: { ...CHANGES_CONTENT_PROPS, openIsDocument: true, docRendererMissing: true },
        expect: '文档预览未启用',
      },
    ],
  },
  {
    id: 'changes-all-view',
    title: 'ChangesAllView',
    about: '改动那一格没打开具体文件时的那一面：先列这次一共改了多少、改了哪几份，再把每一份的差异全摊开。',
    file: 'src/components/panels/ChangesAllView.vue',
    component: ChangesAllView,
    needs: UI,
    args: {},
    states: [
      {
        name: '整支活的改动',
        note: '顶上一行是文件数和增删行数，下面按文件一段一段地列；宿主塞进来的那一块（这次交付的情况）在插槽里，这里没给。',
        props: CHANGES_ALL_PROPS,
        expect: '个文件 · +',
      },
      {
        name: '这一轮什么都没改',
        note: '没有改动时收起来的不只是清单：一行总数和文件那几颗也一起收，只留一句「暂无改动」。',
        props: { ...CHANGES_ALL_PROPS, treeFiles: [], diffByPath: new Map(), fileDiffs: [] },
        expect: '暂无改动',
      },
    ],
  },
  {
    id: 'panel-changes-shell',
    title: 'PanelChanges',
    about: '任务页右侧「改动」那一格的薄壳：收 usePanelChanges 的整包，摊开递给 PanelChangesView。',
    file: 'src/components/panels/PanelChanges.vue',
    component: PanelChanges,
    needs: UI,
    args: { topicId: DEMO_TOPIC },
    states: [
      {
        name: '一份文件自己的 diff',
        note: '包里是一支改了三份文件的活，打开着那份新建的 README：壳子把四十来样状态一样不落地接给了 View。',
        props: { changes: changesBundle() },
        expect: '这个项目放本课程的课件和作业',
      },
      {
        name: '这一轮什么都没改',
        note: '包里的树和差异都是空的：两边都写「暂无改动」。',
        props: { changes: changesBundle(NOTHING_CHANGED) },
        expect: '暂无改动',
      },
      {
        name: '没绑仓库',
        note: '取数那一层说这个项目没有代码仓库：一句话说清，不画一棵空树；树和打开的文件都是空的，横条上文件那半也不摆。',
        props: { changes: changesBundle(NO_REPO) },
        expect: '暂无代码仓库',
      },
    ],
  },
  {
    id: 'panel-doc-shell',
    title: 'PanelDoc',
    about: '话题页右侧「文档」那一格的薄壳：收文档、评论串、名册三包，摊开递给 PanelDocView。',
    file: 'src/components/panels/PanelDoc.vue',
    component: PanelDoc,
    needs: UI,
    args: {
      topic: DOC_TOPIC,
      activityTick: 0,
      topicList: [DOC_TOPIC],
      agentName: PANEL_NAMES.cheese,
      agentHandle: 'cheese-demo',
      docThreads: docThreadsBundle(),
      docPeople: docPeopleBundle(),
    },
    states: [
      {
        name: '房间的文档',
        note: '标题是话题名，正文是一篇协同文档；名册那一包是真的 useDocPeople 算出来的。',
        props: {
          docPanel: docBundle({
            session: docSession(
              '## 课程资料\n\n每周一更新一次，作业在这里登记。\n\n- 第一周：读书报告\n- 第二周：小组讨论'
            ),
          }),
        },
        expect: '每周一更新一次',
      },
      {
        name: '资料库里的一份文档',
        note: '给了 document 就打开这一份：标题不再是话题名，而是页上一行能直接改的输入框，里面是它自己的名字（壳子另记一份，改名时先改这里再回写）。',
        props: {
          document: { id: 'doc-syllabus', projectId: DEMO_PROJECT, title: '课程大纲' },
          docPanel: docBundle({ session: docSession('第一周到第十六周的安排。') }),
        },
        expect: '第一周到第十六周的安排。',
      },
      {
        name: '没有编辑权限',
        note: '包里说只读：横条上写着只读，点不回编辑。',
        props: { docPanel: docBundle({ session: docSession('只能看，不能改。'), editable: false, readOnly: true }) },
        expect: '只读',
      },
    ],
  },
  {
    id: 'panel-preview-shell',
    title: 'PanelPreview',
    about: '话题页右侧「预览」那一格的薄壳：收 usePanelPreview 的整包，摊开递给 PanelPreviewView。',
    file: 'src/components/panels/PanelPreview.vue',
    component: PanelPreview,
    needs: UI,
    args: { topicId: DEMO_TOPIC, projectId: DEMO_PROJECT, frameName: 'cheese-preview-demo' },
    states: [
      {
        name: '一篇 markdown',
        note: '包里是芝士最后摆出来的那份课件：正文直接画出来，帧那几样是取数那一层的初值。',
        props: { preview: previewBundle() },
        expect: '课件和作业都在这里',
      },
      {
        name: '还没有东西可看',
        note: '房间里还没摆出过任何东西：一句话，不是一块空白。',
        props: {
          preview: previewBundle({ previewFile: null, previewMime: '', previewNamed: false, previewNamedPath: '' }),
        },
        expect: '暂无预览',
      },
      {
        name: '取预览失败',
        note: '读这一格本身失败：一句「预览加载失败」，底下一行是这个错。',
        props: {
          preview: previewBundle({ previewFile: null, previewMime: '', previewError: '接口返回 502' }),
        },
        expect: '预览加载失败',
      },
    ],
  },
  {
    id: 'panel-routines',
    title: 'PanelRoutines',
    about: '话题页右侧「例行事务」那一格：这个频道的规则，新建、暂停、看执行记录都从这里抛出去。',
    file: 'src/components/panels/PanelRoutines.vue',
    component: PanelRoutines,
    needs: ['vuetify', 'i18n'],
    args: { defaultRoom: DEMO_TOPIC },
    states: [
      {
        name: '两条在跑',
        note: '一条按时间跑（写着下一次什么时候），一条等频道里发生某件事。',
        props: { routines: [ROUTINE_ACCEPTED, ROUTINE_WEEKLY] },
        expect: '成果被采纳时',
      },
      {
        name: '展开了执行记录',
        note: '最近三次：成了的那次写着交回的文件名，没能执行的那次把原因收在「原因 ▸」后面。',
        props: { routines: [ROUTINE_WEEKLY], runs: ROUTINE_RUNS, openId: ROUTINE_WEEKLY.id },
        expect: '原因',
      },
      {
        name: '删不掉的那一条',
        note: '这一条不归你管：别的动作都在行尾的 ⋯ 里，就是不画「删除」。',
        props: { routines: [{ ...ROUTINE_WEEKLY, can_delete: false }] },
        expectSelector: 'button[aria-label="更多操作"]',
      },
      {
        name: '还没有规则',
        note: '空的时候告诉人两条路：点「新建」，或者在频道里让芝士起草。',
        props: { routines: [] },
        expect: '还没有例行事务',
      },
    ],
  },
  {
    id: 'panel-site',
    title: 'PanelSite',
    about: '话题页右侧「现场」那一格：AI 队友干活的实况，一轮一组，每一步一行，说的话照对话栏渲染。',
    file: 'src/components/panels/PanelSite.vue',
    component: PanelSite,
    needs: ['vuetify', 'i18n'],
    args: { topicId: DEMO_TOPIC, projectId: DEMO_PROJECT, memberNames: PANEL_NAMES, agentName: PANEL_NAMES.cheese },
    states: [
      {
        name: '一轮正在跑',
        note: '剧本第三步：开工作区、看一眼、写 README、提交推送；这一轮还在跑，组头上亮着「进行中」。',
        props: {
          site: siteBundle({ connected: true }),
          working: true,
          runningTurns: SITE_WORKING.running,
        },
        expect: '进行中',
      },
      {
        name: '一轮做完了',
        note: '递了验收卡，这一轮收尾：组头写几步、用了多久，平台动作的圆点是实心的。',
        props: { site: siteBundle({ transcript: SITE_DONE.site }) },
        expect: 'docs: add a welcome note',
      },
      {
        name: '读记录的时候',
        note: '还在路上时画的是那条流的形状（一条条「圆点 + 动作 + 参数」的单行），不是一个居中的转圈。',
        props: { site: siteBundle({ transcript: [], loading: true }) },
        expect: '加载中',
        expectSelector: '.skel--site',
      },
      {
        name: '还没有记录',
        note: '这个房间还没人干过活：会话栏照常在顶上，底下一句话。',
        props: { site: siteBundle({ transcript: [] }) },
        expect: '暂无现场记录',
      },
    ],
  },
  {
    id: 'site-agent-tabs',
    title: 'SiteAgentTabs',
    about: '现场顶上按队友筛的那一排：一个房间里几个队友并行时，切一个看他做过什么；只有一个队友时整排不画。',
    file: 'src/components/panels/SiteAgentTabs.vue',
    component: SiteAgentTabs,
    needs: ['i18n'],
    args: { agents: SITE_AGENTS, labels: SITE_AGENT_LABELS, controls: 'demo-site-log' },
    states: [
      {
        name: '两个队友',
        note: '默认停在「全部」：几段轮次交错着排，哪一段是谁做的由每组的组头署。',
        props: { viewing: null },
        expect: '芝士K',
        expectSelector: '.site-agents__tab--on',
      },
      {
        name: '只看一个队友',
        note: '选中的那一格填成实心、字提亮；底下那条记录就只剩他这一段。',
        props: { viewing: 'cheese-k' },
        expect: '全部',
      },
    ],
  },
  {
    id: 'site-turn-head',
    title: 'SiteTurnHead',
    about: '现场一轮开头的那一行：谁、什么时候、几步、用了多久，底下那根细条说这一轮的时间花在哪几段。',
    file: 'src/components/panels/SiteTurnHead.vue',
    component: SiteTurnHead,
    needs: ['i18n'],
    args: { turn: SITE_ONE_TURN, startsAtMs: SITE_TURN_START },
    states: [
      {
        name: '一轮做完了',
        note: '几步、用了多久写在这里（用时从这一轮开始那一刻算，不是从头一步，不然「思考中」等掉的那十几秒就不见了）。',
        props: { who: '芝士', live: false },
        expect: '5 步',
        expectSelector: '.turn__phases',
      },
      {
        name: '一轮还在跑',
        note: '右头亮一个「进行中」，那个点是呼吸的（浏览器里看得出来）。',
        props: { who: '芝士', live: true },
        expect: '进行中',
      },
      {
        name: '只有一个队友的房间',
        note: '`who` 由父组件算：只看一个队友、或者房间里只有一个时不给名字，组头上就不写。',
        props: { who: null, live: false },
        expect: '5 步',
      },
    ],
  },
  {
    id: 'site-act-row',
    title: 'SiteActRow',
    about:
      '现场里的一步：一格圆点、一个动作词、一段参数，右头是时刻。失败的那句错另起一行，平台自己做的事圆点是实心的。',
    file: 'src/components/panels/SiteActRow.vue',
    component: SiteActRow,
    needs: ['i18n'],
    args: { memberNames: PANEL_NAMES, topicId: DEMO_TOPIC, load: loadLsOutput },
    states: [
      {
        name: '改一个文件',
        note: '动词是这一步做的事，参数是它指着哪一样 —— 现场一行就这两样，扫一眼知道到哪儿了。',
        props: { block: SITE_ACT_EDIT, expanded: false },
        expect: 'landing.css',
      },
      {
        name: '平台的动作',
        note: '芝士调平台接口（发消息、开工作区）也是它做的事，圆点画成实心的，和自己起的工具分得开。',
        props: { block: SITE_ACT_PLATFORM, expanded: false },
        expect: '主按钮已改成琥珀色',
        expectSelector: '.site-act--platform',
      },
      {
        name: '这一步失败了',
        note: '那句错另起一行、缩进到这一步底下，不藏在提示里：卡在哪一眼看得见。',
        props: { block: SITE_ACT_FAILED, expanded: false },
        expect: '执行器连接中断，结果未知',
      },
      {
        name: '摊开一步的输出',
        note: '收着时只说这一步打印了多少字节，第一次点开才去取那一段（输出那一行是它自己那一件）。',
        props: { block: SITE_ACT_BASH_OUTPUT, expanded: true },
        expect: `输出（${LS_OUTPUT_BYTES} B）`,
      },
    ],
  },
  {
    id: 'site-say-row',
    title: 'SiteSayRow',
    about: '现场里「芝士说了一句话」的那一行：头像、名字、时刻，正文照对话栏渲染，里面的引用点得动。',
    file: 'src/components/panels/SiteSayRow.vue',
    component: SiteSayRow,
    needs: ['i18n'],
    args: { memberNames: PANEL_NAMES, agentName: PANEL_NAMES.cheese },
    states: [
      {
        name: '短的一句话',
        note: '一句话占一行，署名写的是开口的那个人（名册上查不到的 AI 落成项目队友的名字）。',
        props: { block: SITE_SAY_SHORT, long: false, expanded: false },
        expect: '收到补充：手机上也要检查。',
      },
      {
        name: '长的一段收成 12 行',
        note: '父组件量出来越过 12 行那条线（`long`）就收：正文折到 12 行、末尾淡出，底下那颗说清全部有多少行。',
        props: { block: SITE_SAY_DETAIL, long: true, expanded: false },
        expect: '展开全部（14 行）',
      },
      {
        name: '摊开之后',
        note: '摊开就不再收，那颗按钮改说「收起」。',
        props: { block: SITE_SAY_DETAIL, long: true, expanded: true },
        expect: '收起',
      },
    ],
  },
  {
    id: 'panel-threads',
    title: 'PanelThreads',
    about: '频道概览里「支线」那一格：有人回过话的支线，最近有回复的在前，转成任务的写那件任务。',
    file: 'src/components/panels/PanelThreads.vue',
    component: PanelThreads,
    // 顶上那颗「新建支线」（清单第 4 条）是 Vuetify 的按钮，得把 vuetify 装进来。
    needs: UI,
    args: {
      rows: THREAD_ROWS,
      loading: false,
      error: null,
      refs: { mentionNames: PANEL_NAMES, topicTitles: {} },
      nameOf: (handle: string) => PANEL_NAMES[handle] ?? handle,
      fmtTime: threadTime,
      canCreate: true,
    },
    states: [
      {
        name: '两条支线',
        note: '每一行写挂着的那条消息、最后一句回复和谁说过话；有新回复的亮一个点；出了任务的那一条在回复的位置改写它出的任务和各自的状态。',
        props: {},
        expect: '任务「整理第一周的课件」',
      },
      {
        name: '读支线的时候',
        note: '手上还一行都没有：骨架，不是转圈。',
        props: { rows: [], loading: true },
        expect: '加载中',
        expectSelector: '.skel--list',
      },
      {
        name: '读失败',
        note: '一行都没拿到时把错说出来；已经有行时出错不盖掉它们。',
        props: { rows: [], error: '加载失败，稍后重试' },
        expect: '加载失败，稍后重试',
      },
      {
        name: '还没有支线',
        note: '这个频道里还没有人回过话：空态给一个「新建支线」。',
        props: { rows: [] },
        expect: '暂无支线',
      },
      {
        name: '外部协作者看支线',
        note: '开不了支线的人没有顶上那一颗，空态也不给动作。',
        props: { rows: [], canCreate: false },
        expect: '暂无支线',
      },
    ],
  },
  {
    id: 'project-file-view',
    title: 'ProjectFileView',
    about: '频道里点一枚文件 chip 开的那一格：项目当前版本里的这份文件，只读；不在当前版本里就指到改过它的任务。',
    file: 'src/components/panels/ProjectFileView.vue',
    component: ProjectFileView,
    needs: UI,
    states: [
      {
        name: '一份读得到的文件',
        note: '代码和文本在只读编辑器里打开（带行号的 chip 会滚到那几行并选中）；顶上写着这是项目当前版本。',
        props: projectFileProps({ lines: { start: 2, end: 2 } }),
        // 正文在 Monaco 里，它是按需加载的，测试环境里不画字：看的是只读编辑器那一块在不在。
        expectSelector: '.code-editor',
      },
      {
        name: '读文件的时候',
        note: '转圈：等的可能是代码、图片或一份文档，到了才知道是哪一种。',
        props: projectFileProps({ loading: true }),
        expectSelector: '[role="progressbar"]',
      },
      {
        name: '不在当前版本里',
        note: '这份文件多半是某件任务里新建、还没合进项目的：说清这一点，再列出改过它的任务。',
        props: projectFileProps({ path: 'README.md', missing: true, tasks: [CHANGES_TASK] }),
        expect: '在任务「整理第一周的课件」里看',
      },
      {
        name: '文件太大',
        note: '画不出来的不硬画：说清为什么，给一颗下载原文件。',
        props: projectFileProps({ path: 'slides/week-1.mp4', tooLarge: true, bytes: 48 * 1024 * 1024, content: '' }),
        expect: '文件过大，无法在浏览器中打开',
      },
      {
        name: '读不到',
        note: '取文件失败：错误原话摆在这一格里。',
        props: projectFileProps({ error: '读取文件失败：请求超时' }),
        expect: '读取文件失败：请求超时',
      },
    ],
  },
  {
    id: 'site-step-output',
    title: 'SiteStepOutput',
    about: '现场里摊开一步之后那一行「输出」：默认收着、写着有多大，第一次点开才去取。',
    file: 'src/components/panels/SiteStepOutput.vue',
    component: SiteStepOutput,
    needs: [],
    states: [
      {
        name: '一小段输出',
        note: '收着时只说有多长：参数是这一行的主体，输出是想追问的人才看的。点开是 ls 打印的那两行。',
        props: { blockId: 'demo-ls', bytes: LS_OUTPUT_BYTES, load: loadLsOutput },
        expect: `输出（${LS_OUTPUT_BYTES} B）`,
      },
      {
        name: '一大段输出',
        note: '几十 KB 的构建日志也只占一行；点开时后端只留了末尾 8 KB，会再说一句只看到了末尾。',
        props: { blockId: 'demo-build', bytes: BUILD_LOG_BYTES, load: loadBuildTail },
        expect: '输出（48 KB）',
      },
    ],
  },
  {
    id: 'todo-checklist',
    title: 'TodoChecklist',
    about: '一份步骤清单的那几行：房间总览里的「进度」和任务卡上的「进度」都画它。',
    file: 'src/components/panels/TodoChecklist.vue',
    component: TodoChecklist,
    needs: UI,
    states: [
      {
        name: '刚列出来',
        note: '剧本第二步：芝士复述完理解就列了三步，第一步正在做（半填充、字加粗），其余空心圈。',
        props: { items: checklistAt(1) },
        expect: '读一下项目现有文件',
        expectSelector: '.progress-item--in_progress:first-child',
      },
      {
        name: '做到一半',
        note: '第一步打了勾、字变淡，做到第二步。',
        props: { items: checklistAt(2) },
        expect: '写 README.md',
        expectSelector: '.progress-item--completed + .progress-item--in_progress',
      },
      {
        name: '全做完了',
        note: '三步都带勾：清单本身不写「完成」，那句结果写在消息里。',
        props: { items: checklistAt(3) },
        expect: '递验收卡',
        expectSelector: '.progress-item--completed:last-child',
      },
    ],
  },
  ...DOC_PANEL_ENTRIES,
  ...PREVIEW_PANEL_ENTRIES,
]
