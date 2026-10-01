// 一份规则的取数：读（项目总览 / 某个房间）、做（确认、暂停、恢复、立即执行、删除、
// 新建、修改）、以及执行记录什么时候去读。
//
// 两处用它，问的是同一个问题、打的是同一个接口，只是范围不同：
//   - 项目的「定时与触发」页 → 整个项目（`topicId` 不给）
//   - 房间右侧「定时与触发」那一格 → 只这一个房间（`topicId` 给了）
// 所以画的那一半（`components/routine/RoutineBoard.vue`）只有一份，取数也就只有一份。
//
// 画的人不在这里：这一层不碰 DOM，也不认识 Vuetify。`can_manage` 是后端逐条给的
// （规则主人或项目管理员），这一层原样传下去，不自己算 —— 「谁能动这条规则」是后端的
// 判断，前端算一遍就是第二个答案。
import type { Routine, RoutineInput, RoutineRun } from '../lib/routine'

import { computed, ref, watch } from 'vue'

import {
  createRoutine,
  deleteRoutine,
  getRoutine,
  listProjectRoutines,
  routineAction,
  updateRoutine,
} from '../api/routines'

import { t } from '@/i18n'

export interface RoutineListOptions {
  projectId: string
  /** 只看这一个房间；`null` / 不给 = 整个项目的总览。 */
  topicId?: string | null
  /** 父层在一轮结束后加一：规则可能刚被芝士起草出来，重读一遍。 */
  tick?: number
}

export function useRoutineList(options: RoutineListOptions) {
  const routines = ref<Routine[]>([])
  const loading = ref(false)
  /** 读列表失败。行内动作的失败也落在这里，摆在列表上面那一条。 */
  const error = ref('')
  /** 正在做的那件事，`${id}:${action}`。同一条规则上同一时刻只可能有一件。 */
  const busy = ref('')
  /** 展开着执行记录的那一条。 */
  const openId = ref<string | null>(null)
  const runs = ref<Record<string, RoutineRun[]>>({})

  // 等确认的那几条单独摆在前面：它们此刻什么都没在跑，摆在「已经在跑」那一堆里
  // 就没人看得见，而它们是这一页唯一需要人动手的东西。
  const drafts = computed(() => routines.value.filter((r) => r.state === 'draft'))
  const others = computed(() => routines.value.filter((r) => r.state !== 'draft'))

  function fail(e: unknown, fallback: string) {
    error.value = e instanceof Error ? e.message : fallback
  }

  async function reload() {
    // 房间面板是工作面板的一部分，而工作面板在「话题还没选定」的那一帧也在：此刻
    // 没有项目可问，问了只会拿到一个 404。等话题到了，上面那次 watch 会重来一遍。
    if (!options.projectId) return
    loading.value = true
    error.value = ''
    try {
      const listed = await listProjectRoutines(options.projectId, options.topicId)
      routines.value = listed.data
    } catch (e) {
      fail(e, t('routines.loadFailed'))
    } finally {
      loading.value = false
    }
  }

  /** 展开 / 收起一条规则的执行记录。展开就现读一次：记录是做完一轮才有的。 */
  async function toggleRuns(r: Routine, force = false) {
    if (openId.value === r.id && !force) {
      openId.value = null
      return
    }
    openId.value = r.id
    try {
      const detail = await getRoutine(r.id)
      runs.value = { ...runs.value, [r.id]: detail.runs }
    } catch (e) {
      fail(e, t('routines.runsFailed'))
    }
  }

  async function act(r: Routine, action: 'confirm' | 'pause' | 'resume' | 'run-now') {
    busy.value = `${r.id}:${action}`
    error.value = ''
    try {
      const out = await routineAction(r.id, action)
      if (action === 'run-now') await toggleRuns(r, true)
      else routines.value = routines.value.map((x) => (x.id === r.id ? (out as Routine) : x))
    } catch (e) {
      fail(e, t('routines.actionFailed'))
    } finally {
      busy.value = ''
    }
  }

  async function remove(r: Routine) {
    busy.value = `${r.id}:delete`
    error.value = ''
    try {
      await deleteRoutine(r.id)
      routines.value = routines.value.filter((x) => x.id !== r.id)
    } catch (e) {
      fail(e, t('routines.deleteFailed'))
    } finally {
      busy.value = ''
    }
  }

  /**
   * 新建 / 修改。
   *
   * 和上面几个不一样：**出错往外抛**，不写进 `error`。表单要的是「这条消息写在它
   * 自己的对话框里」，人改了字段就地再存一次；摆在列表顶上那一条离输入框太远。
   */
  async function save(input: { id?: string; room: string; body: RoutineInput }) {
    if (input.id) {
      const updated = await updateRoutine(input.id, input.body)
      routines.value = routines.value.map((x) => (x.id === updated.id ? updated : x))
      return updated
    }
    const created = await createRoutine(input.room, input.body)
    routines.value = [...routines.value, created]
    return created
  }

  // ---- 那张新建 / 修改的表单 ----
  // 表单开着没有、改的是哪一条、上一次存失败说了什么，和列表一样是这一份状态的一部分：
  // 两个入口（项目总览页、房间右侧那一格）共用同一张 `RoutineFormDialog`，各自再写一遍
  // 这三样就成了两份会走样的状态。
  const formOpen = ref(false)
  /** 正在改的那一条；`null` 是新建。 */
  const editing = ref<Routine | null>(null)
  const formError = ref('')
  const saving = ref(false)

  function startNew() {
    editing.value = null
    formError.value = ''
    formOpen.value = true
  }

  function startEdit(r: Routine) {
    if (!r.can_manage) return
    editing.value = r
    formError.value = ''
    formOpen.value = true
  }

  function closeForm() {
    formOpen.value = false
  }

  /** 表单存下去。出错写在这张表里，人改了字段就地再存一次。 */
  async function submit(payload: { room: string; body: RoutineInput }) {
    if (!editing.value && !payload.room) {
      formError.value = t('routines.pickRoom')
      return
    }
    saving.value = true
    formError.value = ''
    try {
      await save({ id: editing.value?.id, room: payload.room, body: payload.body })
      formOpen.value = false
    } catch (e) {
      formError.value = e instanceof Error ? e.message : t('routines.saveFailed')
    } finally {
      saving.value = false
    }
  }

  watch(
    [() => options.projectId, () => options.topicId],
    () => {
      routines.value = []
      runs.value = {}
      openId.value = null
      void reload()
    },
    { immediate: true }
  )
  // 一轮里芝士起草了一条规则：这一页上的名单要跟上，不然人得自己刷新才看得见。
  watch(
    () => options.tick,
    () => void reload()
  )

  return {
    routines,
    drafts,
    others,
    loading,
    error,
    busy,
    openId,
    runs,
    formOpen,
    editing,
    formError,
    saving,
    reload,
    toggleRuns,
    act,
    remove,
    save,
    startNew,
    startEdit,
    closeForm,
    submit,
  }
}
