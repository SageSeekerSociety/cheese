<script setup lang="ts">
// 执行记录（整页）：一条规则跑过的每一次，一行一次。
//
// 卡片底下那行「看全部 47 次」开的就是这里。记录会一直长，所以既不往下滚也不折叠：
// 一页 10 次（一行 40 像素、10 行 400 像素，加顶上那排筛子和底下那条翻页栏不到一屏），
// 放不下的翻页，页码可以填着跳。顶上三颗筛子只做三件事 —— 倒着看、只看没跑成的、看最近
// 多久 —— 换一颗一律回到第 1 页：留在第 4 页上看的是另一段记录，人还以为看的是同一段。
//
// 取数、翻页、筛子都在这里；画面是 `ProjectRoutineRunsViewView.vue`（路由页拆成容器 +
// 画面两半，见 docs/manual/dev/scenes.md），一行一次执行是 `components/routine/
// RoutineRunRow.vue` —— 卡片里那半页铺的是同一个。
import type { Routine, RoutineRun } from '@/lib/routine'

import { computed, ref, watch } from 'vue'

import { getRoutine, listRoutineRuns } from '../api/routines'

import ProjectRoutineRunsViewView from './ProjectRoutineRunsViewView.vue'

import { t } from '@/i18n'
import { routineRoomTarget } from '@/lib/routine'

/** 一页 10 次：一行 40 像素、10 行 400 像素，加顶栏和翻页栏不到一屏。 */
const PER_PAGE = 10

// 项目 id 是地址里的一层（路由表 `routines/:routineId/runs`）。这一页自己不用它 ——
// 规则自己带着 project_id，去哪儿看结果是它说了算 —— 写在这里是为了别让它落成根元素
// 上的一个属性。
const props = defineProps<{ projectId: string; routineId: string }>()

const routine = ref<Routine | null>(null)
const runs = ref<RoutineRun[]>([])
const total = ref(0)
const page = ref(1)
const pageCount = computed(() => Math.max(1, Math.ceil(total.value / PER_PAGE)))
const order = ref<'newest' | 'oldest'>('newest')
const failedOnly = ref(false)
/** 最近几天；null 是全部。默认 30 天 —— 每天跑一次的规则，一个月就是三十来行。 */
const days = ref<number | null>(30)
const loading = ref(false)
/** 规则和记录各记各的那一句读失败：两个请求分开出错时才不会互相盖掉。 */
const routineError = ref('')
const runsError = ref('')
const error = computed(() => routineError.value || runsError.value)
/** 结果文件那一行的去处：这一页没有能把文件摊开的工作面板，去了频道才点得开。 */
const roomTo = computed(() => (routine.value ? routineRoomTarget(routine.value) : undefined))

// 后发的请求赢：翻页快了点两下，先发的那次回来得晚，不能拿它盖掉后发的那一次。
let seq = 0

async function loadRuns() {
  const mine = ++seq
  loading.value = true
  try {
    const res = await listRoutineRuns(props.routineId, {
      pageNo: page.value,
      perPage: PER_PAGE,
      order: order.value,
      failed: failedOnly.value,
      days: days.value,
    })
    if (mine !== seq) return
    runs.value = res.data
    total.value = res.total
    runsError.value = ''
  } catch (e) {
    if (mine !== seq) return
    runsError.value = e instanceof Error ? e.message : t('routines.runsFailed')
  } finally {
    if (mine === seq) loading.value = false
  }
}

/** 规则本身：页头那句「共几次」和结果文件的去处都靠它。进这一页读一次，翻页不重读。 */
async function loadRoutine() {
  const routineId = props.routineId
  try {
    const r = await getRoutine(routineId)
    if (routineId !== props.routineId) return
    routine.value = r
    routineError.value = ''
  } catch (e) {
    if (routineId !== props.routineId) return
    routineError.value = e instanceof Error ? e.message : t('routines.loadFailed')
  }
}

/** 换筛子：回到第 1 页再读。 */
function refilter(change: () => void) {
  change()
  page.value = 1
  void loadRuns()
}

/** 读失败后那一次重试：两样一起重来。 */
function retry() {
  routineError.value = ''
  runsError.value = ''
  void loadRoutine()
  void loadRuns()
}

/** 翻到第几页：越界就落在最近的那一头，不为了往外翻一格把整页清空。 */
function goToPage(n: number) {
  const next = Math.min(Math.max(n, 1), pageCount.value)
  if (next === page.value) return
  page.value = next
  void loadRuns()
}

// 地址换到另一条规则（从 A 的记录走到 B 的）：规则、记录、页码全部重来。同一个组件
// 接着用，所以这里得自己清 —— 不清就会先把上一条的记录铺在新页头底下。
watch(
  () => props.routineId,
  () => {
    routine.value = null
    runs.value = []
    total.value = 0
    page.value = 1
    routineError.value = ''
    runsError.value = ''
    void loadRoutine()
    void loadRuns()
  },
  { immediate: true }
)
</script>

<template>
  <ProjectRoutineRunsViewView
    :routine="routine"
    :runs="runs"
    :total="total"
    :page="page"
    :page-count="pageCount"
    :order="order"
    :failed-only="failedOnly"
    :days="days"
    :loading="loading"
    :error="error"
    :room-to="roomTo"
    @update:page="goToPage"
    @update:order="(v) => refilter(() => (order = v))"
    @update:failed-only="(v) => refilter(() => (failedOnly = v))"
    @update:days="(v) => refilter(() => (days = v))"
    @retry="retry"
  />
</template>
