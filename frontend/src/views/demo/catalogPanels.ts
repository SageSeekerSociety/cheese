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
import {
  BUILD_LOG_BYTES,
  CHANGES_TASK,
  changesBundle,
  checklistAt,
  diffLinesOf,
  docBundle,
  docPeopleBundle,
  docThreadsBundle,
  fileTreeRows,
  loadBuildTail,
  loadLsOutput,
  LONG_DIFF_LINES,
  LS_OUTPUT_BYTES,
  PANEL_NAMES,
  previewBundle,
  projectFileProps,
  ROUTINE_DRAFT,
  ROUTINE_RUNS,
  ROUTINE_WEEKLY,
  SITE_DONE,
  SITE_WORKING,
  siteBundle,
  THREAD_ROWS,
  threadTime,
} from './catalogPanelsFixtures'
import { DEMO_PROJECT, DEMO_TOPIC } from './demoPanels'

import ChangesDiff from '@/components/panels/ChangesDiff.vue'
import ChangesFileTree from '@/components/panels/ChangesFileTree.vue'
import PanelChanges from '@/components/panels/PanelChanges.vue'
import PanelDoc from '@/components/panels/PanelDoc.vue'
import PanelPreview from '@/components/panels/PanelPreview.vue'
import PanelRoutines from '@/components/panels/PanelRoutines.vue'
import PanelSite from '@/components/panels/PanelSite.vue'
import PanelThreads from '@/components/panels/PanelThreads.vue'
import ProjectFileView from '@/components/panels/ProjectFileView.vue'
import SiteStepOutput from '@/components/panels/SiteStepOutput.vue'
import TodoChecklist from '@/components/panels/TodoChecklist.vue'

const UI: CatalogNeed[] = ['vuetify']

export const PANEL_ENTRIES: CatalogEntry[] = [
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
      showAll: false,
      expandedDirs: new Set<string>(),
      activePath: null,
      revealTick: 0,
      cover: false,
      emptyLabel: '暂无改动',
    },
    states: [
      {
        name: '只看改动',
        note: '一份清单，文件夹一律摊开：新增、删除写成字，改过的给 +N −M；打开着的那一份高亮。',
        props: { rows: fileTreeRows(false), activePath: 'README.md' },
        expect: '新增',
      },
      {
        name: '全部文件，文件夹收着',
        note: '看整个工作区：这一支没碰过的文件也在（syllabus.md、slides），文件夹默认收着，只露出第一层；要哪个开哪个。',
        props: { rows: fileTreeRows(true), showAll: true, emptyLabel: '暂无文件' },
        expect: 'syllabus.md',
        expectSelector: '.file-item--dir[title="docs"] .mdi-folder-outline',
      },
      {
        name: '全部文件，展开一个文件夹',
        note: '展开的文件夹贡献它的子树，下一层缩进一格：改过的、删掉的、没碰过的（week-2.md）排在一起，只有前两样带标记。',
        props: {
          rows: fileTreeRows(true, ['docs']),
          showAll: true,
          expandedDirs: new Set(['docs']),
          emptyLabel: '暂无文件',
        },
        expect: 'week-2.md',
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
        note: '包里的树和提交记录都是空的：树上写「暂无改动」，右边写「暂无提交」。',
        props: { changes: changesBundle(NOTHING_CHANGED) },
        expect: '暂无提交',
      },
      {
        name: '没绑仓库',
        note: '取数那一层说这个项目没有代码仓库：一句话说清，不画一棵空树；树、提交、打开的文件都是空的，横条上文件那半也不摆。',
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
    about: '话题页右侧「定时与触发」那一格：这个房间的规则，新建、确认、暂停、看执行记录都从这里抛出去。',
    file: 'src/components/panels/PanelRoutines.vue',
    component: PanelRoutines,
    needs: ['vuetify', 'i18n'],
    args: { defaultRoom: DEMO_TOPIC, userNames: PANEL_NAMES },
    states: [
      {
        name: '一条在跑、一条等确认',
        note: '芝士起草的那条排在最上面「等你确认」；在跑的那条写着下一次什么时候。',
        props: { routines: [ROUTINE_DRAFT, ROUTINE_WEEKLY] },
        expect: '等你确认',
      },
      {
        name: '展开了执行记录',
        note: '最近两次：一次成了（写着做了什么），一次没成（写着为什么）。',
        props: { routines: [ROUTINE_WEEKLY], runs: ROUTINE_RUNS, openId: ROUTINE_WEEKLY.id },
        expect: '工作电脑没有连上',
      },
      {
        name: '别人管的规则',
        note: '这一条不归你管：行里不画按钮，只说清归谁管 —— 只读是说出来，不是偷偷禁用。',
        props: { routines: [{ ...ROUTINE_WEEKLY, can_manage: false }] },
        expect: '只有他和项目管理员能改',
      },
      {
        name: '还没有规则',
        note: '空的时候告诉人两条路：点「新建」，或者在频道里让芝士起草。',
        props: { routines: [] },
        expect: '还没有定时或触发规则',
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
    id: 'panel-threads',
    title: 'PanelThreads',
    about: '频道概览里「支线」那一格：有人回过话的支线，最近有回复的在前，转成任务的写那件任务。',
    file: 'src/components/panels/PanelThreads.vue',
    component: PanelThreads,
    needs: [],
    args: {
      rows: THREAD_ROWS,
      loading: false,
      error: null,
      refs: { mentionNames: PANEL_NAMES, topicTitles: {} },
      nameOf: (handle: string) => PANEL_NAMES[handle] ?? handle,
      fmtTime: threadTime,
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
        note: '这个频道里还没有人在一条消息下面回过话。',
        props: { rows: [] },
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
]
