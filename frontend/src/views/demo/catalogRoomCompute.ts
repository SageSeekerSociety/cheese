/**
 * 房间在哪台工作电脑上跑（`TopicComputePicker`）和它落脚的页头（`task/TaskHeader`）在
 * 预览站里的条目。
 *
 * 和别的条目没有两样（「这是什么 / 在哪儿 / 需要哪几样 / 看哪几格」，规矩见
 * `catalog.ts`），单独一份是因为 `catalog.ts` 已经八百多行、两条塞进去会顶到
 * `frontend/src` 那一千行的上限 —— 和 `catalogSettingsData.ts`、`catalogQueue.ts`
 * 同一个理由。数据就地写着：两条很轻，不值得再开一份 fixtures。
 *
 * 为什么这两件值得一站：它们原先都自己取数（各 `import ../api`，`frontend_grade.py`
 * 的 C 级），想看一眼都得把整个房间或整个任务页拉起来。现在两件都只吃 props、只往上
 * 发事件（A 级）：`TopicComputePicker` 的保存落在 `composables/useTopicComputeChoice.ts`
 * 里，`TaskHeader` 的机器那一项再由 `views/workspace/useTaskPage.ts` 递下来。于是每
 * 一件、每一种有意义的状态都能单独摆在预览站里。
 *
 * 两件的菜单与展开都在**点开之后**才有内容，所以预览站里看到的是它们歇着的那个样子：
 * 一块「改」、一行任务头。要连菜单一起看，是 `TopicComputePicker.spec.ts` 和
 * `task/TaskHeader.spec.ts` 里的活。
 *
 * 这里的 `CatalogEntry` 是 type-only 引用：`catalog.ts` 反过来要 `ROOM_COMPUTE_ENTRIES`
 * 这个值，运行时不构成循环。
 */
import type { PoolListing, RoomTask, Topic, TopicMemberRow } from '@/cx_types'
import type { ComputeChoice, TopicComputeDevice, TopicComputeProfile } from '@/types/compute'
import type { CatalogEntry, CatalogNeed } from './catalog'

import TaskHeader from '@/components/task/TaskHeader.vue'
import TopicComputePicker from '@/components/TopicComputePicker.vue'

/** 两件都要 vuetify（`v-menu` / `v-card` / `v-icon` / `useDisplay`），也都要 i18n
 *  （`t('work.roomMachine.*')`、`t('work.task.*')`）。 */
const UI_T: CatalogNeed[] = ['vuetify', 'i18n']

// ---- 房间的工作电脑（TopicComputePicker）------------------------------------

const CLOUD: ComputeChoice = { name: null, profile: 'cloud', device_id: null }
const DEVICE: ComputeChoice = { name: '实验室工作站', profile: 'device', device_id: 'lab' }

/** 两台自有设备：一台在线（也是机主给自己登记的），一台离线。 */
const DEVICES: TopicComputeDevice[] = [
  { device_id: 'lab', name: '实验室工作站', online: true, owned: true, sandbox_unavailable: null },
  { device_id: 'home', name: '家里那台', online: false },
]

function pool(id: string, label: string, description: string, isDefault: boolean): PoolListing {
  return { kind: 'compute', id, label, tier: 'byo', price: '自备', description, available: true, default: isDefault }
}

/** 房间这一项眼下读回来的样子。默认：落在云端、没点名任何一台设备。 */
function roomProfile(over: Partial<TopicComputeProfile> = {}): TopicComputeProfile {
  return {
    choice: CLOUD,
    project_default: CLOUD,
    current: 'cloud',
    device_id: null,
    devices: DEVICES,
    sessions: [],
    profiles: [
      pool('device', '自有设备', '在你自己连接的机器上跑。', false),
      pool('cloud', '云端环境', '为房间创建一台云端工作电脑。', true),
    ],
    cloud_vm_available: false,
    visibility: { options: [], effective: null, machine_access: false },
    ...over,
  }
}

/** 组件那一份 props：父层把 `useTopicComputeChoice.ts` 的四个读口递下来，`select`
 *  接住往上的选择。 */
function pickerProps(over: Partial<Record<string, unknown>> = {}) {
  return { profile: roomProfile(), saving: false, error: '', proposal: '', savedVersion: 0, ...over }
}

// ---- 任务页头（task/TaskHeader）---------------------------------------------

const ROOM: Topic = {
  id: 'r1',
  project_id: 'p1',
  parent_id: null,
  title: '前端',
  kind: 'topic',
  status: 'active',
  created_at: '2026-10-05T00:00:00Z',
}

function task(over: Partial<RoomTask> = {}): RoomTask {
  return {
    id: 't1',
    project_id: 'p1',
    room_id: 'r1',
    title: '登录页加「记住我」',
    title_source: 'human',
    status: 'open',
    owner_handle: 'wangning',
    created_at: '2026-10-05T00:00:00Z',
    updated_at: '2026-10-05T00:00:00Z',
    presentation: { column: 'not_started', phrase: 'discussing' },
    ...over,
  }
}

const PEOPLE: TopicMemberRow[] = [
  { topic_id: 'r1', member_handle: 'wangning', role: 'owner' },
  { topic_id: 'r1', member_handle: 'alice', role: 'member' },
]

/** 任务页头这一件的全部 props：负责人能做的几件事在真页面上由 `useTaskPage.ts` 给，
 *  这里各给一个空的占位。 */
function taskHeaderProps(over: Partial<RoomTask> = {}) {
  return {
    room: ROOM,
    task: task(over),
    memberNames: { wangning: '王宁', alice: '爱丽丝' },
    agentName: '芝士',
    people: PEOPLE,
    agents: [] as TopicMemberRow[],
    machine: roomProfile({ current: 'device', choice: DEVICE, device_id: 'lab' }),
    machineError: false,
    machineSaving: false,
    machinePickError: '',
    machineProposal: '',
    machineSavedVersion: 0,
    starting: false,
    startError: null,
    actionError: null,
    start: async () => {},
    close: async () => true,
    reopen: async () => true,
    handOver: async () => true,
    rename: async () => true,
    setCollaborators: async () => true,
    setAgent: async () => true,
    loadMachine: async () => {},
    selectMachine: () => {},
  }
}

export const ROOM_COMPUTE_ENTRIES: CatalogEntry[] = [
  {
    id: 'room-compute-picker',
    title: 'TopicComputePicker',
    about: '房间（或任务）在哪台工作电脑上跑：默认云端、一台自有设备、整台云虚拟机都在这里换；点名一台还能给不给整台机器。',
    file: 'src/components/TopicComputePicker.vue',
    component: TopicComputePicker,
    needs: UI_T,
    states: [
      {
        name: '默认落在云端',
        note: '歇着的那个样子：名册行尾一颗「改」。点开才有清单、机器访问那两行和「其他配置与设备」。',
        props: pickerProps(),
        expectSelector: '.cp-action',
      },
      {
        name: '房间钉在一台自有设备上，机主能给整台机器',
        note: 'current 是设备、点名了那台、机主本人登记过：点开后有一栏「这个房间在 实验室工作站 上能看到什么」。',
        props: pickerProps({
          profile: roomProfile({
            current: 'device',
            choice: DEVICE,
            device_id: 'lab',
            visibility: { options: [], effective: 'isolated', machine_access: false },
          }),
        }),
        expectSelector: '.cp-action',
      },
      {
        name: '一次选择变成了一条提议',
        note: '撞上项目档位策略时接口照样 200，换来的是一条等人点头的提议，不是错误，也不重取。',
        props: pickerProps({ proposal: '这一步等 @andyl 点头。' }),
        expectSelector: '.cp-action',
      },
      {
        name: '上次选择被拒了',
        note: '后端没让换（例如正在准备环境）：原话留着，菜单不收，让他看着那句话再挑一次。',
        props: pickerProps({ error: '正在准备环境，稍后再换' }),
        expectSelector: '.cp-action',
      },
      {
        name: '一次保存还在路上',
        note: 'saving 为真：每一行都灰着，再点也没有用。',
        props: pickerProps({ saving: true }),
        expectSelector: '.cp-action',
      },
    ],
  },
  {
    id: 'task-header',
    title: 'TaskHeader',
    about: '任务页的页头：任务叫什么、此刻在哪一格、谁负责、谁在协作；负责人在这里开始、转交、关闭，也在这里换做它的电脑。',
    file: 'src/components/task/TaskHeader.vue',
    component: TaskHeader,
    needs: UI_T,
    states: [
      {
        name: '讨论中的一条任务',
        note: '歇着的那个样子：房间名、任务名、此刻那一格写「讨论中」，右边是负责人那一颗（点开是详情）。',
        props: taskHeaderProps(),
        expect: '登录页加「记住我」',
      },
      {
        name: '已经关了的一条任务',
        note: 'status 是 closed：负责人那一颗还在（详情照看），只是不再有「开始」，也不给换队友。',
        props: taskHeaderProps({ status: 'closed', started_at: '2026-10-05T01:00:00Z', started_by: 'wangning' }),
        expect: '登录页加「记住我」',
      },
    ],
  },
]
