/**
 * 「改动」里批注那几件在预览站里的条目：写批注的框、挂在行下面的那一条、存的时候
 * 和 AI 队友的修改重叠时逐处选版本的那一块。
 *
 * 单独一份，理由同 `catalogAccept.ts`：`catalog.ts` 已经顶着行数上限。三件都只吃
 * props、只往上发事件，批注从哪来、送到哪去在 `composables/useReviewComments.ts`。
 */
import type { DiffReview, MergeRegion, OfficeComparison, ReviewComment } from '@/types/reviewComment'
import type { CatalogEntry, CatalogNeed } from './catalog'

import OfficeCompare from '@/components/review/OfficeCompare.vue'
import ReviewCommentBox from '@/components/review/ReviewCommentBox.vue'
import ReviewCommentCard from '@/components/review/ReviewCommentCard.vue'
import ReviewDocument from '@/components/review/ReviewDocument.vue'
import ReviewMergePicker from '@/components/review/ReviewMergePicker.vue'
import { revisionsBundle } from '@/test/panelBundles'

const UI_T: CatalogNeed[] = ['vuetify', 'i18n']

const BASE: ReviewComment = {
  id: 'c-1',
  author: 'pengwenbo',
  path: 'backend/app/domain/notification/repositories.py',
  line_start: 208,
  line_end: 208,
  line_text: '        q = q.where(Notice.answered_at.is_(None))',
  place: 'L208',
  current_line: 208,
  body: '归档的任务也要排除，不然侧栏还会数到它',
  suggestion: null,
  parent_id: null,
  state: 'draft',
  card_id: null,
  sent_at: null,
  outcome: null,
  outcome_note: null,
  created_at: '2026-10-08T10:00:00Z',
}

const SUGGESTION: ReviewComment = {
  ...BASE,
  id: 'c-2',
  path: 'frontend/src/views/SignupForm.vue',
  line_start: 30,
  line_end: 31,
  line_text: '  <!-- 院系改为选填 -->\n',
  body: '院系不能去掉，老师要按院系统计',
  suggestion: '  <DeptSelect v-model="dept" required />\n',
}

const HANDLED: ReviewComment = {
  ...BASE,
  id: 'c-3',
  state: 'sent',
  card_id: 'card-1',
  sent_at: '2026-10-08T10:05:00Z',
  outcome: 'handled',
  outcome_note: '加了 archived_at 条件',
}

const REGIONS: MergeRegion[] = [
  { kind: 'same', text: '<template>\n' },
  {
    kind: 'conflict',
    base: '  <p class="hint">请输入手机号</p>\n',
    mine: '  <p class="hint">手机号为 11 位数字</p>\n',
    theirs: '  <p class="hint" v-if="!phone">必填</p>\n',
  },
  { kind: 'same', text: '</template>\n' },
]

const WORD: OfficeComparison = {
  kind: 'word',
  new_file: false,
  identical: false,
  changed: 2,
  against: 'returned',
  rows: [
    { op: 'same', before: 1, after: 1, text: '3.2 实验结果' },
    {
      op: 'changed',
      before: 2,
      after: 2,
      pieces: [
        { op: 'equal', text: '在 120 名参与者中，完成全部任务的有 ' },
        { op: 'delete', text: '96' },
        { op: 'insert', text: '98' },
        { op: 'equal', text: ' 人。' },
      ],
    },
    { op: 'added', before: null, after: 3, text: '平均用时比 A 组少 4.2 分钟（p = 0.03）。' },
  ],
  formatting: [{ after: 1, text: '3.2 实验结果' }],
}

const SHEET: OfficeComparison = {
  kind: 'sheet',
  new_file: false,
  identical: false,
  changed: 2,
  sheets: [
    {
      name: '汇总',
      status: 'changed',
      cells: [
        { address: 'C3', before: '34', after: '33', before_formula: null, after_formula: null, formula: false },
        {
          address: 'C5',
          before: '96',
          after: '95',
          before_formula: 'SUM(C2:C4)',
          after_formula: 'SUM(C2:C3)',
          formula: true,
        },
      ],
    },
  ],
}

const SLIDES: OfficeComparison = {
  kind: 'slide',
  new_file: false,
  identical: false,
  changed: 2,
  slides: [
    {
      op: 'changed',
      before: 1,
      after: 1,
      title: '研究问题',
      pieces: [
        { op: 'insert', text: '报名表单的' },
        { op: 'equal', text: '字段越少，完成率越高吗' },
      ],
    },
    { op: 'added', before: null, after: 2, title: '实验结果' },
    { op: 'same', before: 2, after: 3, title: '局限' },
  ],
}

const DOC_COMMENT: ReviewComment = {
  ...BASE,
  id: 'c-doc',
  path: 'docs/结题报告.docx',
  line_start: 3,
  line_end: 3,
  line_text: '98 人',
  place: 'p3',
  current_line: 3,
  body: '和问卷汇总表对不上，那边是 96',
}

const DOC_REVIEW: DiffReview = {
  comments: [DOC_COMMENT],
  writable: true,
  me: 'pengwenbo',
  busy: false,
  agentName: '芝士',
}

export const REVIEW_ENTRIES: CatalogEntry[] = [
  {
    id: 'office-compare',
    title: 'OfficeCompare',
    about: '一份 Office 文件和上一版比：Word 按段落两栏对齐，表格列出变了的格子，幻灯片按页标出改过和新加的。',
    file: 'src/components/review/OfficeCompare.vue',
    component: OfficeCompare,
    needs: UI_T,
    states: [
      {
        name: 'Word',
        note: '同一段落在同一行；只改了格式的段落列在最下面。',
        props: { comparison: WORD },
        expect: '只改了格式',
      },
      {
        name: '表格',
        note: '原来的值和现在的值并排，变的是公式的标出来。',
        props: { comparison: SHEET },
        expect: '汇总',
      },
      {
        name: '幻灯片',
        note: '按阅读顺序，改过的页带着字词级的差异。',
        props: { comparison: SLIDES },
        expect: '实验结果',
      },
    ],
  },
  {
    id: 'review-document',
    title: 'ReviewDocument',
    about: '「改动」里打开的一份 Office 文件：左边是这一版或和上一版的对比，右边是修订和批注。',
    file: 'src/components/review/ReviewDocument.vue',
    component: ReviewDocument,
    needs: UI_T,
    args: { path: 'docs/结题报告.docx', documentType: null, docBytes: null, review: DOC_REVIEW, canCompare: true },
    states: [
      {
        name: '对比退回时那一版',
        note: '被退回过的交付和退回时那一版比；右边挂着这份文件上的批注。',
        props: { revs: revisionsBundle(), comparing: true, comparison: WORD },
        expect: '对比退回时那一版',
      },
    ],
  },

  {
    id: 'review-comment-box',
    title: 'ReviewCommentBox',
    about: '在差异里选中几行之后插在下面的那个框：写批注，或者点「修改建议」直接改成想要的样子。',
    file: 'src/components/review/ReviewCommentBox.vue',
    component: ReviewCommentBox,
    needs: UI_T,
    states: [
      {
        name: '刚点开',
        note: '「修改建议」没按下时只有一段话；按下后选中的那几行原样填进下面的等宽框。',
        props: { lineText: BASE.line_text, submitLabel: '添加批注' },
        expect: '修改建议',
      },
      {
        name: '写修改建议',
        note: '编辑一条带修改建议的批注时，框一打开就是建议的写法。',
        props: { lineText: SUGGESTION.line_text, suggestion: SUGGESTION.suggestion, submitLabel: '保存' },
        expect: '保存',
      },
    ],
  },
  {
    id: 'review-comment-card',
    title: 'ReviewCommentCard',
    about: '挂在它说的那几行下面的一条批注：自己没送出的能改能删，上一轮的带着芝士说的处理结果。',
    file: 'src/components/review/ReviewCommentCard.vue',
    component: ReviewCommentCard,
    needs: UI_T,
    args: { where: '第 208 行', agentName: '芝士', writable: true },
    states: [
      {
        name: '自己的，未发送',
        note: '只有写的人看得见，退回时一起送出。',
        props: { comment: BASE, mine: true },
        expect: '未发送',
      },
      {
        name: '修改建议',
        note: '原来的几行和建议的几行画成一小段差异。',
        props: { comment: SUGGESTION, mine: true, where: '第 30–31 行' },
        expect: '你的修改建议',
      },
      {
        name: '上一轮的，已处理',
        note: '芝士重新交上来时逐条说了处理没有，这一句跟在批注下面；待审阅时能回复。',
        props: { comment: HANDLED, mine: false },
        expect: '已处理',
      },
    ],
  },
  {
    id: 'review-merge-picker',
    title: 'ReviewMergePicker',
    about: '存的时候和 AI 队友这段时间的修改重叠了：每一处三栏并排，逐处选一个版本再存。',
    file: 'src/components/review/ReviewMergePicker.vue',
    component: ReviewMergePicker,
    needs: UI_T,
    states: [
      {
        name: '一处重叠',
        note: '默认选你的；点 AI 队友那一栏就换成它的。',
        props: { regions: REGIONS, agentName: '芝士' },
        expect: '你打开时',
      },
    ],
  },
]
