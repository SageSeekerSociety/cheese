/**
 * 预览站里个人芝士的那几格：面板、输入框里的 @ 菜单、设置里的「芝士」页。
 *
 * 从 `catalogSpaceTasks.ts` 分出来，那一册已经顶到一千行的上限；条目的规矩见 `catalog.ts`。
 * `CatalogEntry`、`CatalogNeed` 是 type-only 引用，运行时不构成循环。
 */
import type { Citable } from '@/types/citable'
import type { CatalogEntry, CatalogNeed } from './catalog'

import { ref } from 'vue'

import { MOST_CITED, useCitePicker } from '@/composables/useCitePicker'

import CatalogAgentRules from './CatalogAgentRules.vue'
import { PANEL_DONE, PANEL_LIVE, PANEL_SPLIT } from './catalogSpaceTasksFixtures'
import {
  ASSISTANT_MEMORY,
  ASSISTANT_RECENT,
  ASSISTANT_ROUTINES,
  ASSISTANT_RULES,
} from './catalogViewSettingsWorkspaceFixtures'

import AssistantPanel from '@/components/assistant/AssistantPanel.vue'
import CiteMenu from '@/components/assistant/CiteMenu.vue'
import { t } from '@/i18n'
import AssistantView from '@/views/user/settings/AssistantView.vue'

/** @ 菜单的例子候选（假数据），眼前是项目「知是」。 */
const CITE_ITEMS: Citable[] = [
  { id: 'a1', kind: 'task', title: '接入搜索', where: '知是', place: '后端' },
  { id: 'a2', kind: 'task', title: '修复回答中插话', where: '知是', place: '后端' },
  { id: 'a3', kind: 'task', title: '接入数据集', where: '机器学习课程设计', place: '实验' },
  { id: 'c1', kind: 'channel', title: '设计', where: '知是' },
  { id: 'd1', kind: 'doc', title: '10 月周报', where: '知是' },
  { id: 'd2', kind: 'doc', title: '搜索接入方案', where: '知是' },
  { id: 'file-f1', kind: 'file', title: 'cheese自建周报-2026-10-19.md', where: '我的资料库' },
]

/** @ 菜单的一格：输入框里打着 `text`，候选是上面几样，眼前是项目「知是」。 */
function citeDemo(text: string) {
  const items = CITE_ITEMS
  return useCitePicker({
    draft: ref(text),
    caret: ref(text.length),
    items: () => items,
    recents: () => ['a1', 'd1', 'c1', 'a3', 'file-f1'],
    project: () => '知是',
    most: MOST_CITED,
    opened: () => {},
    picked: () => {},
    scrollActiveIntoView: () => {},
  })
}

/** 自动化卡片的例子：让芝士等「接入搜索」提交审阅后先过一遍改动。 */
const RULE_CARD = { id: 'w1', when: '任务「接入搜索」提交审阅时', note: '读改动，对照任务要求列出问题' }
const RULE_ASKED = {
  role: 'user',
  text: '等「接入搜索」提交审阅了，帮我先过一遍改动，看看有没有明显的问题',
  at: '2026-10-25T09:00:00Z',
}
const RULE_MADE = {
  role: 'assistant',
  text: '好的。任务「接入搜索」提交审阅时，我会在这段对话里读改动、对照任务要求列出问题，再通知你。',
  at: '2026-10-25T09:00:08Z',
  rules: [RULE_CARD],
}
const RULE_FIRED = [
  {
    role: 'platform',
    text: '',
    at: '2026-10-25T14:32:00Z',
    shown: { wait: '审阅接入搜索的提交', task: '接入搜索', event: 'filed' },
  },
  {
    role: 'assistant',
    text: '看完了，有两处要注意：\n\n1. 任务要求失败时重试一次，改动里没有重试，超时直接报错。\n2. 新加的搜索接口没有测试。\n\n其余和要求一致。要我把这两点写成审阅意见吗？',
    at: '2026-10-25T14:33:10Z',
  },
]

/** 画法要 Vuetify、文案要语言包。 */
const UI_T: CatalogNeed[] = ['vuetify', 'i18n']

/** 上面那些，外加一条 name 指得上的路由（按钮、链接按名字指路时用）。 */
const UI_T_R: CatalogNeed[] = ['vuetify', 'i18n', 'router']

export const VIEW_ASSISTANT_ENTRIES: CatalogEntry[] = [
  {
    id: 'assistant-panel',
    title: 'AssistantPanel',
    get about() {
      return t('catalog.assistantPanel.about')
    },
    file: 'src/components/assistant/AssistantPanel.vue',
    component: AssistantPanel,
    needs: UI_T_R,
    args: {
      messages: [],
      streaming: null,
      tool: null,
      notice: null,
      busy: false,
      context: '',
      get greeting() {
        return t('tasks.assistant.draft.greeting')
      },
      get starters() {
        return [t('tasks.assistant.draft.starters.rubric'), t('tasks.assistant.draft.starters.check')]
      },
      get placeholder() {
        return t('tasks.assistant.draft.placeholder')
      },
      get hint() {
        return t('tasks.assistant.draft.hint')
      },
      attachAsMaterials: true,
    },
    states: [
      {
        get name() {
          return t('catalog.assistantPanel.s1.name')
        },
        get note() {
          return t('catalog.assistantPanel.s1.note')
        },
        props: {},
        expect: '起草提交内容和评分要点',
      },
      {
        get name() {
          return t('catalog.assistantPanel.s2.name')
        },
        get note() {
          return t('catalog.assistantPanel.s2.note')
        },
        props: { ...PANEL_LIVE, busy: true, streaming: '' },
        expect: '填写题目',
      },
      {
        get name() {
          return t('catalog.assistantPanel.s3.name')
        },
        get note() {
          return t('catalog.assistantPanel.s3.note')
        },
        props: PANEL_DONE,
        expect: '待你确认',
      },
      {
        get name() {
          return t('catalog.assistantPanel.s4.name')
        },
        get note() {
          return t('catalog.assistantPanel.s4.note')
        },
        props: PANEL_SPLIT,
        expectSelector: '.at__card--one',
      },
      {
        get name() {
          return t('catalog.assistantPanel.s5.name')
        },
        get note() {
          return t('catalog.assistantPanel.s5.note')
        },
        props: {
          ...PANEL_DONE,
          earlier: [{ id: 'old', title: '实验三从哪入手', at: '2026-10-19T08:00:00Z', messages: PANEL_DONE.messages }],
          hasOlder: true,
          dockable: true,
          docked: true,
        },
        expect: '更早的话题',
      },
      {
        get name() {
          return t('catalog.assistantPanel.s6.name')
        },
        get note() {
          return t('catalog.assistantPanel.s6.note')
        },
        // 在项目的频道里问：芝士读了记录，起草一条回复和一个任务，都等人确认。
        props: {
          messages: [
            { role: 'user', text: '我三天没看，有什么需要我处理的？', at: '2026-10-21T08:00:00Z' },
            {
              role: 'assistant',
              text: '和你有关的有两件：李老师问演示视频能否由你负责；路线规划超时还没有人认领。',
              at: '2026-10-21T08:00:12Z',
              steps: [
                { key: 'c1', verb: '搜索聊天记录', arg: '演示视频', state: 'done' },
                { key: 'c2', verb: '起草消息', arg: '', state: 'done' },
                { key: 'c3', verb: '起草任务', arg: '排查路线规划超时', state: 'done' },
              ],
              proposals: [
                {
                  id: 1,
                  kind: 'message',
                  state: 'pending',
                  where: '综合',
                  text: '@李老师 演示视频我来负责，11月7日前给出初版。',
                },
                {
                  id: 2,
                  kind: 'task',
                  state: 'sent',
                  where: '综合',
                  title: '排查路线规划超时',
                  text: '复现超时，超时 3 秒后重试一次，补测试。',
                  task: 't-1',
                },
              ],
            },
          ],
        },
        expect: '待你确认',
      },
      {
        get name() {
          return t('catalog.assistantPanel.s7.name')
        },
        get note() {
          return t('catalog.assistantPanel.s7.note')
        },
        // 例行事务的一次运行：平台开的，芝士起草了一条回复留给人确认；上一次留下的已过期。
        props: {
          earlier: [
            {
              id: 'run-1',
              title: '有人找我',
              at: '2026-10-21T09:00:00Z',
              messages: [
                {
                  role: 'platform',
                  text: '',
                  at: '2026-10-21T09:00:00Z',
                  shown: { routine: '有人找我' },
                },
                {
                  role: 'assistant',
                  text: '赵一鸣在「综合」问周报什么时候交，我起草了一条回复。',
                  at: '2026-10-21T09:00:20Z',
                  proposals: [{ id: 9, kind: 'message', state: 'expired', where: '综合', text: '周五前交。' }],
                },
              ],
            },
          ],
          messages: [
            { role: 'platform', text: '', at: '2026-10-22T09:00:00Z', shown: { routine: '有人找我' } },
            {
              role: 'assistant',
              text: '李老师在「综合」@ 你，问演示视频能否由你负责。你建这条例行事务时没有说可以替你答应，我起草了一条回复，等你确认。',
              at: '2026-10-22T09:00:18Z',
              steps: [
                { key: 'r1', verb: '读取', arg: 'ml-course/channels/1', state: 'done' },
                { key: 'r2', verb: '发消息', arg: 'ml-course/channels/1', state: 'done' },
              ],
              proposals: [
                {
                  id: 10,
                  kind: 'message',
                  state: 'pending',
                  where: '综合',
                  text: '@李老师 演示视频我来负责，11月7日前给出初版。',
                },
              ],
            },
          ],
        },
        expect: '已过期',
      },
      {
        get name() {
          return t('catalog.assistantPanel.s8.name')
        },
        get note() {
          return t('catalog.assistantPanel.s8.note')
        },
        // 芝士记下一条关于你的事：面板里一张「记忆」卡片，能撤销。
        props: {
          messages: [
            { role: 'user', text: '记住，我给老师写东西都要用书面语，别太口语。', at: '2026-10-22T10:00:00Z' },
            {
              role: 'assistant',
              text: '记住了。以后替你写给老师的消息和邮件都用书面语。',
              at: '2026-10-22T10:00:06Z',
              steps: [{ key: 'm1', verb: '读写记忆', arg: '给老师写东西用书面语', state: 'done' }],
              changes: [
                {
                  id: 21,
                  draft: -21,
                  created: false,
                  name: '',
                  title: '记忆',
                  canUndo: true,
                  rows: [{ change: 21, key: 'memory', label: '已记住', value: '给老师写东西用书面语', undone: false }],
                  notes: [],
                },
              ],
            },
          ],
        },
        expect: '已记住',
      },
      {
        get name() {
          return t('catalog.assistantPanel.s9.name')
        },
        get note() {
          return t('catalog.assistantPanel.s9.note')
        },
        // 一件长的事正在做：最上面是清单；先说了一句，派出两个分身，一个还在做，一个交回了；
        // 最后一组都回来了，芝士在想下一步。
        props: {
          messages: [
            {
              role: 'user',
              text: '把这 40 份实验报告按评分标准各给一个分，写一句理由，汇总成一张表给我。',
              at: '2026-10-24T08:00:00Z',
            },
          ],
          busy: true,
          streaming: '我先读评分标准，再把报告分成两批，派两个分身同时打分。',
          steps: [
            {
              key: 't1',
              verb: '更新进度清单',
              arg: '',
              state: 'done',
              at: 0,
              hidden: true,
              checklist: [
                { content: '读评分标准，整理出评分项', status: 'completed' },
                { content: '给第 1–20 份报告打分', status: 'in_progress' },
                { content: '给第 21–40 份报告打分', status: 'in_progress' },
                { content: '汇总成表，标出分数异常的几份', status: 'pending' },
              ],
            },
            { key: 'r1', verb: '读取', arg: '评分标准.pdf', state: 'done', at: 0 },
            {
              key: 'a1',
              verb: '派出分身',
              arg: '给第 1–20 份报告打分',
              state: 'run',
              at: 27,
              subagent: {
                status: 'running',
                steps: [
                  { key: 'a1-1', verb: '读取', arg: '报告 01.pdf', state: 'done' },
                  { key: 'a1-2', verb: '读取', arg: '报告 02.pdf', state: 'done' },
                ],
              },
            },
            {
              key: 'a2',
              verb: '派出分身',
              arg: '给第 21–40 份报告打分',
              state: 'done',
              at: 27,
              subagent: {
                status: 'done',
                steps: [{ key: 'a2-1', verb: '读取', arg: '报告 21.pdf', state: 'done' }],
              },
            },
          ],
        },
        expect: '进行中',
      },
      {
        get name() {
          return t('catalog.assistantPanel.s10.name')
        },
        get note() {
          return t('catalog.assistantPanel.s10.note')
        },
        // 做完了：清单收成一行，两个分身都交回了，话和步骤按发生的顺序排开。
        props: {
          messages: [
            {
              role: 'user',
              text: '把这 40 份实验报告按评分标准各给一个分，写一句理由，汇总成一张表给我。',
              at: '2026-10-24T08:00:00Z',
            },
            {
              role: 'assistant',
              text: '我先读评分标准，再把报告分成两批，派两个分身同时打分。\n\n40 份都打完了，平均 78 分。第 7、19、33 份分数明显偏低，理由写在表里。成绩表已经发给你。',
              at: '2026-10-24T08:31:00Z',
              steps: [
                {
                  key: 't1',
                  verb: '更新进度清单',
                  arg: '',
                  state: 'done',
                  at: 0,
                  hidden: true,
                  checklist: [
                    { content: '读评分标准，整理出评分项', status: 'completed' },
                    { content: '给第 1–20 份报告打分', status: 'completed' },
                    { content: '给第 21–40 份报告打分', status: 'completed' },
                    { content: '汇总成表，标出分数异常的几份', status: 'completed' },
                  ],
                },
                { key: 'r1', verb: '读取', arg: '评分标准.pdf', state: 'done', at: 0 },
                {
                  key: 'a1',
                  verb: '派出分身',
                  arg: '给第 1–20 份报告打分',
                  state: 'done',
                  at: 27,
                  subagent: {
                    status: 'done',
                    steps: [{ key: 'a1-1', verb: '读取', arg: '报告 01.pdf', state: 'done' }],
                  },
                },
                {
                  key: 'a2',
                  verb: '派出分身',
                  arg: '给第 21–40 份报告打分',
                  state: 'done',
                  at: 27,
                  subagent: {
                    status: 'done',
                    steps: [{ key: 'a2-1', verb: '读取', arg: '报告 21.pdf', state: 'done' }],
                  },
                },
                { key: 'g1', verb: '发送文件', arg: '成绩表.xlsx', state: 'done', at: 27 },
              ],
            },
          ],
        },
        expect: '4/4 项已完成',
      },
      {
        get name() {
          return t('catalog.assistantPanel.s11.name')
        },
        get note() {
          return t('catalog.assistantPanel.s11.note')
        },
        // 芝士在做，人在输入框里又说了一句：按钮是「发送」，这句话插进正在做的这一轮。
        props: {
          messages: [
            { role: 'user', text: '把这 40 份实验报告各给一个分，汇总成表。', at: '2026-10-24T08:00:00Z' },
            { role: 'user', text: '第 30 份以后的不用打了。', at: '2026-10-24T08:12:00Z' },
          ],
          busy: true,
          streaming: '好，第 30 份以后的不打了。',
          steps: [{ key: 'r1', verb: '读取', arg: '报告 12.pdf', state: 'done', at: 0 }],
          // 「+」和麦克风在答的时候也在：选好的文件随这句话一起进这一轮。
          canAttach: true,
          files: [{ name: '第 35 份补交.pdf', mime: 'application/pdf' }],
          hearVoice: async () => ({ text: '', raw: '' }),
        },
        expect: '第 30 份以后的不用打了',
      },
      {
        get name() {
          return t('catalog.assistantPanel.s12.name')
        },
        get note() {
          return t('catalog.assistantPanel.s12.note')
        },
        // 做了事却一个字没写；上面还有更早的消息没读回来。
        props: {
          currentEarlier: true,
          messages: [
            { role: 'user', text: '我现在有哪些事等着我处理？', at: '2026-10-24T09:00:00Z' },
            {
              role: 'assistant',
              text: '',
              silent: true,
              at: '2026-10-24T09:00:20Z',
              steps: [{ key: 'i1', verb: '查看待办', arg: '', state: 'done', at: 0 }],
            },
          ],
        },
        expect: '芝士没有给出回答',
      },
      {
        get name() {
          return t('catalog.assistantPanel.s13.name')
        },
        get note() {
          return t('catalog.assistantPanel.s13.note')
        },
        // 在频道「后端」里：上一句引用了一份文档和一个任务、带着选中的一段；输入框上方是
        // 「当前页面」和这一次又选中的一段。
        props: {
          pageLabel: '频道「后端」',
          selection: '首个字要等很久，所以超时设成 600 秒',
          messages: [
            {
              role: 'user',
              text: '<#d1> 里说的超时和 <#a1> 改的是一回事吗？',
              at: '2026-10-24T09:00:00Z',
              askedOn: '频道「后端」',
              selection: '超时设成 600 秒',
              cited: [
                { id: 'd1', kind: 'doc', title: '搜索接入方案' },
                { id: 'a1', kind: 'task', title: '接入搜索' },
              ],
            },
            {
              role: 'assistant',
              text: '是一回事：方案里写的 600 秒就是任务「接入搜索」里改的模型代理读超时。',
              at: '2026-10-24T09:00:20Z',
            },
          ],
        },
        expect: '超时设成 600 秒',
      },
      {
        get name() {
          return t('catalog.assistantPanel.s14.name')
        },
        get note() {
          return t('catalog.assistantPanel.s14.note')
        },
        props: { pageLabel: '频道「后端」', pageOff: true },
        expect: '频道「后端」',
      },
      {
        get name() {
          return t('catalog.assistantPanel.s15.name')
        },
        get note() {
          return t('catalog.assistantPanel.s15.note')
        },
        // 在输入框里打 @ 就弹出菜单（候选是例子数据）。
        props: {
          pageLabel: '频道「后端」',
          project: '知是',
          citable: CITE_ITEMS,
          recents: ['a1', 'd1', 'c1', 'a3', 'file-f1'],
          cited: { d2: { kind: 'doc', title: '搜索接入方案' } },
        },
        expect: '频道「后端」',
      },
    ],
  },
  {
    id: 'agent-rules',
    title: 'AssistantRuleCard',
    get about() {
      return t('catalog.agentRules.about')
    },
    file: 'src/components/assistant/AssistantRuleCard.vue',
    component: CatalogAgentRules,
    needs: UI_T_R,
    args: { busy: false, streaming: null, tool: null, notice: null, context: '' },
    states: [
      {
        get name() {
          return t('catalog.agentRules.s1.name')
        },
        get note() {
          return t('catalog.agentRules.s1.note')
        },
        props: { states: { w1: { state: 'waiting' } }, messages: [RULE_ASKED, RULE_MADE] },
        expect: '任务「接入搜索」提交审阅时',
      },
      {
        get name() {
          return t('catalog.agentRules.s2.name')
        },
        get note() {
          return t('catalog.agentRules.s2.note')
        },
        props: { states: { w1: { state: 'started' } }, messages: [RULE_ASKED, RULE_MADE, ...RULE_FIRED] },
        expect: '提交审阅了',
      },
      {
        get name() {
          return t('catalog.agentRules.s3.name')
        },
        get note() {
          return t('catalog.agentRules.s3.note')
        },
        props: {
          states: {
            a: { state: 'waiting' },
            b: { state: 'started' },
            c: { state: 'cancelled' },
            d: { state: 'gaveUp' },
            e: { state: 'on' },
            f: { state: 'paused', why: 'credits' },
          },
          messages: [
            {
              role: 'assistant',
              text: '',
              at: '2026-10-25T09:00:08Z',
              rules: ['a', 'b', 'c', 'd']
                .map((id) => ({ ...RULE_CARD, id }))
                .concat(
                  { id: 'e', when: '每次你提的任务被退回时', note: '把审阅意见整理成待办' },
                  { id: 'f', when: '每天 08:00', note: '列出我的待办' }
                ),
            },
          ],
        },
        expect: '已停止等待',
      },
    ],
  },
  {
    id: 'cite-menu',
    title: 'CiteMenu',
    get about() {
      return t('catalog.citeMenu.about')
    },
    file: 'src/components/assistant/CiteMenu.vue',
    component: CiteMenu,
    needs: UI_T,
    args: { picker: citeDemo('@'), project: '知是', most: MOST_CITED },
    states: [
      {
        get name() {
          return t('catalog.citeMenu.s1.name')
        },
        get note() {
          return t('catalog.citeMenu.s1.note')
        },
        props: {},
        expect: '最近去过',
      },
      {
        get name() {
          return t('catalog.citeMenu.s2.name')
        },
        get note() {
          return t('catalog.citeMenu.s2.note')
        },
        props: { picker: citeDemo('@接入') },
        expect: '搜索接入方案',
      },
      {
        get name() {
          return t('catalog.citeMenu.s3.name')
        },
        get note() {
          return t('catalog.citeMenu.s3.note')
        },
        props: { picker: citeDemo('<#a> <#b> <#c> <#d> <#e> @') },
        expect: '最多引用 5 样',
      },
    ],
  },
  {
    id: 'settings-assistant-view',
    title: 'AssistantView',
    get about() {
      return t('catalog.settingsAssistantView.about')
    },
    file: 'src/views/user/settings/AssistantView.vue',
    component: AssistantView,
    needs: UI_T,
    args: {
      rules: ASSISTANT_RULES,
      memory: ASSISTANT_MEMORY,
      routines: ASSISTANT_ROUTINES,
      recent: ASSISTANT_RECENT,
      loading: false,
      failed: false,
      saving: false,
      saved: false,
      error: '',
    },
    states: [
      {
        get name() {
          return t('catalog.settingsAssistantView.s1.name')
        },
        get note() {
          return t('catalog.settingsAssistantView.s1.note')
        },
        props: {},
        expect: '自动化',
      },
      {
        get name() {
          return t('catalog.settingsAssistantView.s2.name')
        },
        get note() {
          return t('catalog.settingsAssistantView.s2.note')
        },
        props: {
          routines: [],
          memory: { enabled: false, items: [] },
          rules: { ...ASSISTANT_RULES, askItems: [], allowItems: [] },
        },
        expect: '暂无自动化',
      },
    ],
  },
]
