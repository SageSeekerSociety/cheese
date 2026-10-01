// Separate entry: production room components, deterministic local data only.
import '../style.css'

import type { AskGroupAction, AskGroupState } from '../lib/askGroupState'

import { createApp, defineComponent, h, reactive } from 'vue'

import AskGroupFlow from '../components/ask/AskGroupFlow.vue'
import { emptyAskDraft } from '../lib/askState'

const scope = {
  topic_id: 'preview-room',
  asked_by: 'preview-agent',
  id: 'preview-group',
  members: ['entry', 'validation'],
  total: 2,
}
const question = ['本轮先接入哪个入口？', '如何安排本轮验证？']
const choices = [
  [
    { text: '接入真实房间', explain: '直接复用房间里的表单组件与组提交契约' },
    { text: '先整理证据', explain: '保留已通过记录，先补缺项' },
  ],
  [
    { text: '只跑新增回归', explain: '覆盖重复提交、刷新恢复与账号隔离' },
    { text: '保留待验证项', explain: '明确写出尚未运行的联调和原生证据' },
  ],
]
const mode = new URLSearchParams(location.search).get('mode') ?? 'draft'
if (new URLSearchParams(location.search).has('dark')) document.documentElement.dataset.theme = 'dark'
const state = reactive<AskGroupState>({
  scope,
  anchor: 'entry',
  pending: null,
  busy: false,
  fresh: true,
  error: mode === 'error' ? '示例网络超时：提交结果未确认，请保留原操作后刷新' : null,
  storageBlocked: false,
  confirm: mode === 'confirm',
  conflict: false,
  unavailable: false,
  forms: Object.fromEntries(
    scope.members.map((id) => [
      id,
      {
        draft: emptyAskDraft(),
        pending: null,
        editing: false,
        busy: false,
        fresh: true,
        saved: false,
        error: null,
        conflict: false,
        storageBlocked: false,
      },
    ])
  ),
  data: {
    group: scope,
    settlement: null,
    receipt: null,
    blocks: scope.members.map((id, index) => ({
      id,
      topic_id: scope.topic_id,
      kind: 'message',
      author_type: 'participant',
      author: scope.asked_by,
      content: question[index]!,
      created_at: '2026-10-01T00:00:00Z',
      meta: {
        options: choices[index],
        asked: 'preview-person',
        allow_other: true,
        reject_option: true,
        answer_log: [],
        ask_group: { ...scope, index },
      },
    })),
  },
})
for (const block of state.data!.blocks) {
  const stored = localStorage.getItem(`ask-preview:${block.id}`)
  if (stored) {
    try {
      state.forms[block.id]!.draft = JSON.parse(stored)
      state.forms[block.id]!.editing = true
      state.forms[block.id]!.saved = true
    } catch {
      state.error = '示例草稿无法读取'
    }
  }
}
if (mode === 'receipt') {
  state.data!.blocks[0]!.meta!.answer_log = [
    {
      v: 1,
      kind: 'option',
      option: choices[0]![0]!.text,
      note: '保留说明和历史',
      by: 'preview-person',
      at: null,
      client_op_id: 'preview-answer-1',
    },
    {
      v: 2,
      kind: 'note',
      option: null,
      note: '更正：先完成真实接线，再补新增证据',
      by: 'preview-person',
      at: null,
      client_op_id: 'preview-answer-2',
    },
  ]
  state.data!.settlement = {
    v: 1,
    by: 'preview-person',
    at: null,
    answered: ['entry'],
    later: ['validation'],
    unanswered: [],
    payload_hash: 'fixture-only',
    client_op_id: 'fixture-only',
    delivery_event_id: 'fixture-only',
  }
  state.data!.receipt = {
    event_id: 'fixture-only',
    state: 'uncertain',
    attempts: 1,
    last_error: '示例：RPC 回应未能证明执行者消费',
    sent_at: null,
    received_at: null,
    completed_at: null,
  }
}
function action(a: AskGroupAction) {
  if (a.type === 'question') {
    const form = state.forms[a.blockId]!
    if (a.action.type === 'draft') {
      form.draft = a.action.draft
      form.editing = true
    }
    if (a.action.type === 'correct') form.editing = true
    if (a.action.type === 'cancel') form.editing = false
    localStorage.setItem(`ask-preview:${a.blockId}`, JSON.stringify(form.draft))
    form.saved = true
  } else if (a.type === 'later') {
    const form = state.forms[a.blockId]!
    form.draft.later = !form.draft.later
    localStorage.setItem(`ask-preview:${a.blockId}`, JSON.stringify(form.draft))
  } else if (a.type === 'back') state.confirm = false
  else if (a.type === 'submit') state.confirm = true
  else if (a.type === 'confirm') {
    state.confirm = false
    state.error = '这是组件演示，不发送后端请求，也不生成执行成功回执'
  }
}
createApp(
  defineComponent({
    setup: () => () =>
      h('main', { style: 'max-width:780px;margin:32px auto;padding:16px' }, [
        h('h1', { style: 'font-size:24px' }, '提问体验：真实组件演示'),
        h('p', '固定本地数据，未连接后端。与真实房间共用 AskGroupFlow 和 AskQuestionForm；回执是标注的示例。'),
        h('p', '点选仅存草稿；整组回去补／照样交；刷新可恢复此演示草稿。'),
        h(AskGroupFlow, { state, viewer: 'preview-person', names: { 'preview-person': '示例答者' }, onAction: action }),
      ]),
  })
).mount('#app')
