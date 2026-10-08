// 任务页页头「AI 队友」那一行的效果图：挂的是真组件、走的是真样式，只有数据是编的。
// 编数据是刻意的——一张图里要同时摆出「跟着房间」「单独指定了某位」「不是负责人只看得见」
// 三种样子，真名册里凑不齐。
import { createApp, defineComponent, h, reactive } from 'vue'
import TaskHeader from '../frontend/src/components/task/TaskHeader.vue'
import vuetify from '../frontend/src/plugins/vuetify'
import { setLocale } from '../frontend/src/i18n'
import '../frontend/src/style.css'

setLocale('zh-CN')

const room = {
  id: 't1',
  title: '召回层选型',
  kind: 'root',
  status: 'active',
  project_id: '11111111-1111-1111-1111-111111111111',
  parent_id: null,
  created_at: '2026-09-01T02:00:00Z',
  last_activity_at: '2026-09-08T02:00:00Z',
  owner_handle: 'alice',
}

const task = reactive<Record<string, unknown>>({
  id: 318,
  topic_id: 't1',
  title: '把召回层的三种方案对比写成一页',
  status: 'open',
  owner_handle: 'alice',
  contributor_handles: ['chiruotong'],
  agent_handle: null,
  started_at: null,
  presentation: { phrase: 'discussing' },
})

const memberNames: Record<string, string> = {
  alice: '爱丽丝',
  chiruotong: '池若彤',
  wangning: '王宁',
  'cheese-2f81a4c9': '小苔',
  'cheese-5b7e0c31': '无言',
}
const people = [
  { member_handle: 'alice', name: '爱丽丝', role: 'lead' },
  { member_handle: 'chiruotong', name: '池若彤', role: 'member' },
  { member_handle: 'wangning', name: '王宁', role: 'member' },
]
const agents = [
  { member_handle: 'cheese-2f81a4c9', agent: true },
  { member_handle: 'cheese-5b7e0c31', agent: true },
]
const machine = {
  choice: { name: null, profile: 'cloud', device_id: null },
  project_default: { name: null, profile: 'cloud', device_id: null },
  current: 'cloud',
  device_id: null,
  devices: [],
  sessions: [],
  profiles: [],
  cloud_vm_available: false,
  visibility: { options: [], effective: null, machine_access: false },
  follows_room: true,
}

const yes = async () => true
const noop = async () => {}

createApp(
  defineComponent({
    setup: () => () =>
      h(TaskHeader as never, {
        room,
        task,
        memberNames,
        agentName: '小苔',
        people,
        agents,
        machine,
        machineError: false,
        starting: false,
        startError: null,
        actionError: null,
        connected: true,
        start: noop,
        close: yes,
        reopen: yes,
        handOver: yes,
        rename: yes,
        // 同上：真的加进去之后那一行要跟着变，图里才看得到结果。
        setCollaborators: async (handles: string[]) => {
          task.contributor_handles = handles
          return true
        },
        // 真页面这里是等后端回话；效果图里直接当成换成了，好让图里看到换完的样子。
        setAgent: async (handle: string | null) => {
          task.agent_handle = handle
          return true
        },
        loadMachine: noop,
        panelOpen: false,
      })
  })
)
  .use(vuetify)
  .mount('#app')
