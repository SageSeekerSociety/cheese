/**
 * 「要你拍个板」那两件（`AskCard`、`AskFlow`）在预览站里的条目。
 *
 * 它们和别的条目没有两样（「这是什么 / 在哪儿 / 需要哪几样 / 看哪几格」，规矩见
 * `catalog.ts`），单独一份是因为这两条加起来三百多行，塞进 `catalog.ts` 会顶到
 * `frontend/src` 那一千行的上限 —— 和 `catalogChat.ts`、`catalogAccept.ts` 同一个
 * 理由。
 *
 * 这两件是**提案**，还没接进产品：摆在预览站里是为了和产品现状并排比对。真实接入
 * 的契约、读写 owner 和反例写在本话题的实现说明里，不在这里重复。
 *
 * 这里的 `CatalogEntry` 是 type-only 引用：`catalog.ts` 反过来要 `ASK_ENTRIES` 这个
 * 值，运行时不构成循环。
 */
import type { CatalogEntry, CatalogNeed } from './catalog'

import AskCard from './AskCard.vue'
import AskFlow from './AskFlow.vue'
import { fakeAnswerService } from './fakeAnswerService'

/** 这两件都要 vuetify（`v-btn` / `v-textarea` / `v-icon`），也都有不写死在模板里的字。 */
const UI: CatalogNeed[] = ['vuetify', 'i18n']

export const ASK_ENTRIES: CatalogEntry[] = [
  {
    id: 'ask-card',
    title: 'AskCard（提案）',
    about:
      '「需要你拍个板」的一张卡：题目、选项、自由输入、选择后提交、稍后处理、已答回执。**尚未接进产品**，摆在预览站里比对用。',
    file: 'src/views/demo/AskCard.vue',
    component: AskCard,
    needs: UI,
    states: [
      {
        name: '① 刚问出来',
        note: '题目一眼看得出这是等你回答的题，不是谁随口说的一句话。选项各自带一句「选了会怎样」。此时「提交回答」按不动。',
        props: {
          question: '数据用哪一份？',
          age: '2 小时了',
          options: [
            { label: '课程发的那份 sales.csv', consequence: '会覆盖项目里 3 处引用' },
            { label: '项目里的演示数据', consequence: '不动任何引用，只是这次先用它' },
          ],
        },
        expect: '等你回答',
      },
      {
        name: '② 选好了，还没交',
        note: '点一下只是选中，不发出去 —— 手滑能反悔、能换。这时「提交回答」才点亮。',
        props: {
          question: '数据用哪一份？',
          age: '2 小时了',
          options: [
            { label: '课程发的那份 sales.csv', consequence: '会覆盖项目里 3 处引用' },
            { label: '项目里的演示数据', consequence: '不动任何引用，只是这次先用它' },
          ],
          initialPicked: 0,
        },
        expect: '点「提交回答」才算答上',
      },
      {
        name: '③ 捎了一句别的',
        note: '自由输入是骑在答案上的那一句，不是旁边另开的一条通道。选哪一项都能带，也可以不带。回车即提交。',
        props: {
          question: '数据用哪一份？',
          options: [
            { label: '课程发的那份 sales.csv', consequence: '会覆盖项目里 3 处引用' },
            { label: '项目里的演示数据', consequence: '不动任何引用，只是这次先用它' },
          ],
          initialPicked: 1,
          initialNote: '先用演示数据，等老师确认了再换',
        },
        expect: '提交回答',
      },
      {
        name: '④ 已答回执',
        note: '答完不消失：谁选了什么、捎了一句什么、它接下来会做什么，都留着。要改还有「改答案」。',
        props: {
          question: '数据用哪一份？',
          options: [
            { label: '课程发的那份 sales.csv', consequence: '会覆盖项目里 3 处引用' },
            { label: '项目里的演示数据', consequence: '不动任何引用，只是这次先用它' },
          ],
          initialPhase: 'answered',
          initialSubmitted: { option: '课程发的那份 sales.csv', note: '就用老师发的这份' },
        },
        expect: '已交给芝士',
      },
      {
        name: '⑤ 稍后处理',
        note: '放着不等于忘了。卡还在，标着它在等你，回来点开就能答 —— 现在的提问没有这个动作，问题就那么挂着。',
        props: {
          question: '数据用哪一份？',
          age: '2 小时了',
          options: [
            { label: '课程发的那份 sales.csv', consequence: '会覆盖项目里 3 处引用' },
            { label: '项目里的演示数据', consequence: '不动任何引用，只是这次先用它' },
          ],
          initialPhase: 'deferred',
        },
        expect: '先放着了',
      },
    ],
  },
  {
    id: 'ask-flow',
    title: 'AskFlow（提案·完整流程）',
    about:
      '需要你拍个板的**完整流程**：多题切换与总进度、每题选项解释、关联自由输入、草稿未提交提示、稍后能找回、提交成功/失败/重试、已答回执与改答案。**尚未接进产品**，摆在预览站里比对用。形状照 Codex 的 request_user_input 工具定义与 TUI 源码。',
    file: 'src/views/demo/AskFlow.vue',
    component: AskFlow,
    needs: UI,
    states: [
      {
        name: '① 三件事摆在一起',
        note: '好几件待拍板的事，看得出一共几件、答了几件。上面一条是切换，答过的、先放着的、没交上的都标出来。',
        props: {
          questions: [
            {
              id: 'data',
              header: '数据用哪份',
              question: '这次跑演示，数据用哪一份？',
              age: '2 小时了',
              options: [
                { label: '课程发的 sales.csv', description: '会覆盖项目里 3 处引用' },
                { label: '项目里的演示数据', description: '不动任何引用，只是这次先用它' },
              ],
            },
            {
              id: 'env',
              header: '跑在哪',
              question: '演示跑在哪台机器上？',
              age: '1 小时了',
              options: [
                { label: '我这台开发机', description: '快，但你走开就断了' },
                { label: '云端那台', description: '慢一点，一直开着' },
              ],
            },
            {
              id: 'who',
              header: '谁来讲',
              question: '下周汇报谁来讲这一段？',
              options: [
                { label: '我来讲', description: '你只出材料' },
                { label: '你来讲', description: '我写好逐字稿' },
              ],
            },
          ],
          initialIndex: 0,
        },
        expect: '等你拍板',
      },
      {
        name: '② 选项解释 + 选好了还没交',
        note: '点一下只是选中，不发出去 —— 手滑能反悔、能换。每项下面一句「选了会怎样」，先看后果再点。选完按钮才亮，写着「回车＝提交」。',
        props: {
          questions: [
            {
              id: 'data',
              header: '数据用哪份',
              question: '这次跑演示，数据用哪一份？',
              age: '2 小时了',
              options: [
                { label: '课程发的 sales.csv', description: '会覆盖项目里 3 处引用' },
                { label: '项目里的演示数据', description: '不动任何引用，只是这次先用它' },
              ],
            },
            {
              id: 'env',
              header: '跑在哪',
              question: '演示跑在哪台机器上？',
              options: [
                { label: '我这台开发机', description: '快，但你走开就断了' },
                { label: '云端那台', description: '慢一点，一直开着' },
              ],
            },
          ],
          initialPicked: { data: 1 },
          initialNotes: { data: '' },
        },
        expect: '草稿没交',
      },
      {
        name: '③ 写了话还没交 + 「以上都不是」',
        note: '选项都不对时，「以上都不是」是界面自动加的那条正路（Codex: the client will add a free-form Other option automatically）。话写在备注里，带着理由照样能交。写了没交，右上角直接写「草稿没交」。',
        props: {
          questions: [
            {
              id: 'data',
              header: '数据用哪份',
              question: '这次跑演示，数据用哪一份？',
              age: '2 小时了',
              options: [
                { label: '课程发的 sales.csv', description: '会覆盖项目里 3 处引用' },
                { label: '项目里的演示数据', description: '不动任何引用，只是这次先用它' },
              ],
            },
            {
              id: 'env',
              header: '跑在哪',
              question: '演示跑在哪台机器上？',
              options: [
                { label: '我这台开发机', description: '快，但你走开就断了' },
                { label: '云端那台', description: '慢一点，一直开着' },
              ],
            },
          ],
          initialPicked: { data: 2 },
          initialNotes: { data: '老师下周才发真数据，先别覆盖' },
        },
        expect: '以上都不是',
      },
      {
        name: '④ 提交失败与重试',
        note: '**假服务预览**：交不上去要看得见，不能假装答上了。选择和备注都还在这儿，「再交一次」就能重试。走的是 `fakeAnswerService`——第一次真被拒、第二次放行，组件自己不造失败。',
        props: {
          submitAnswer: fakeAnswerService({ failFirst: 1 }).submit,
          questions: [
            {
              id: 'data',
              header: '数据用哪份',
              question: '这次跑演示，数据用哪一份？',
              age: '2 小时了',
              options: [
                { label: '课程发的 sales.csv', description: '会覆盖项目里 3 处引用' },
                { label: '项目里的演示数据', description: '不动任何引用，只是这次先用它' },
              ],
            },
            {
              id: 'env',
              header: '跑在哪',
              question: '演示跑在哪台机器上？',
              options: [
                { label: '我这台开发机', description: '快，但你走开就断了' },
                { label: '云端那台', description: '慢一点，一直开着' },
              ],
            },
          ],
          initialPicked: { data: 1 },
          initialFailed: ['data'],
        },
        expect: '没答上',
      },
      {
        name: '⑤ 带着没答的题提交',
        note: 'Codex 的「Submit with N unanswered questions?」：先问一句再交，可以「回去补」，也可以「照样交」。不是默默把空的交出去。',
        props: {
          questions: [
            {
              id: 'data',
              header: '数据用哪份',
              question: '这次跑演示，数据用哪一份？',
              options: [
                { label: '课程发的 sales.csv', description: '会覆盖项目里 3 处引用' },
                { label: '项目里的演示数据', description: '不动任何引用，只是这次先用它' },
              ],
            },
            {
              id: 'env',
              header: '跑在哪',
              question: '演示跑在哪台机器上？',
              options: [
                { label: '我这台开发机', description: '快，但你走开就断了' },
                { label: '云端那台', description: '慢一点，一直开着' },
              ],
            },
          ],
          initialPicked: { data: 1 },
          initialConfirm: true,
        },
        expect: '照样交',
      },
      {
        name: '⑥ 已答回执与改答案',
        note: '答完不消失：谁答的、选了什么、捎了一句什么，都留着，下面一栏还有全部题的「N/M 已答」。改答案是新的一版，原来那版留着不抹掉。',
        props: {
          questions: [
            {
              id: 'data',
              header: '数据用哪份',
              question: '这次跑演示，数据用哪一份？',
              options: [
                { label: '课程发的 sales.csv', description: '会覆盖项目里 3 处引用' },
                { label: '项目里的演示数据', description: '不动任何引用，只是这次先用它' },
              ],
            },
            {
              id: 'env',
              header: '跑在哪',
              question: '演示跑在哪台机器上？',
              options: [
                { label: '我这台开发机', description: '快，但你走开就断了' },
                { label: '云端那台', description: '慢一点，一直开着' },
              ],
            },
          ],
          initialAnswered: {
            data: { option: '项目里的演示数据', note: '先用演示数据，等老师确认了再换', by: '王长鑫', at: '刚刚' },
          },
        },
        expect: '改答案',
      },
      {
        name: '⑦ 稍后处理找得回来',
        note: '「稍后处理」不是消失：题还在上面那条切换里挂着、标着先放着，下面「已答」那栏写着（先放着），回来点开就能答。',
        props: {
          questions: [
            {
              id: 'data',
              header: '数据用哪份',
              question: '这次跑演示，数据用哪一份？',
              age: '2 小时了',
              options: [
                { label: '课程发的 sales.csv', description: '会覆盖项目里 3 处引用' },
                { label: '项目里的演示数据', description: '不动任何引用，只是这次先用它' },
              ],
            },
            {
              id: 'env',
              header: '跑在哪',
              question: '演示跑在哪台机器上？',
              age: '1 小时了',
              options: [
                { label: '我这台开发机', description: '快，但你走开就断了' },
                { label: '云端那台', description: '慢一点，一直开着' },
              ],
            },
          ],
          initialDeferred: ['data'],
          initialIndex: 0,
        },
        expect: '先放着了',
      },
      {
        name: '⑧ 刷新回来草稿还在',
        note: '这一格开着 `persistKey`，会把草稿写进 `localStorage` —— **本次的新增目标，不是 Codex 已有的能力**。真正的浏览器刷新恢复证据在 `ask-ux-preview/reload-proof.mjs`：新页面上下文里真 `Page.reload`，回来看草稿在、且**没被标成已答**。',
        props: {
          questions: [
            {
              id: 'data',
              header: '数据用哪份',
              question: '这次跑演示，数据用哪一份？',
              age: '2 小时了',
              options: [
                { label: '课程发的 sales.csv', description: '会覆盖项目里 3 处引用' },
                { label: '项目里的演示数据', description: '不动任何引用，只是这次先用它' },
              ],
            },
            {
              id: 'env',
              header: '跑在哪',
              question: '演示跑在哪台机器上？',
              age: '1 小时了',
              options: [
                { label: '我这台开发机', description: '快，但你走开就断了' },
                { label: '云端那台', description: '慢一点，一直开着' },
              ],
            },
          ],
          initialPicked: { data: 1 },
          initialNotes: { data: '' },
          persistKey: 'preview-reload',
        },
        expect: '草稿没交',
      },
    ],
  },
]
