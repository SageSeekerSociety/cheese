<script setup lang="ts">
// 一串规则：等确认的几条摆在前面，剩下的按后端给的顺序。项目总览页（整个项目）和房间
// 右侧那一格（只这一个房间）画的是同一串，只是给进来的数组不同。
//
// 只吃 props、只往上抛动作：谁去调接口由调用方办。唯一自己做主的是「删之前问一句」——
// 那是一次确认，不是一次请求。
import type { Routine, RoutineRun } from '@/lib/routine'

import { computed, ref } from 'vue'

import RoutineRow from './RoutineRow.vue'

import { routineRoomTarget } from '@/lib/routine'

const props = withDefaults(
  defineProps<{
    routines: Routine[]
    /** 每条规则最近一次读到的执行记录，键是规则 id。 */
    runs?: Record<string, RoutineRun[]>
    /** 展开着执行记录的那一条。 */
    openId?: string | null
    /** 正在做的那件事，`${id}:${action}`。 */
    busy?: string
    loading?: boolean
    /** 读列表或做动作失败的那句话，摆在最上面。 */
    error?: string
    /** 房间名，键是房间 id。项目总览页给。 */
    roomNames?: Record<string, string>
    /** 结果那几行要不要指回规则所在的房间。房间右侧那一格里指过去就是原地。 */
    linkRooms?: boolean
  }>(),
  { runs: () => ({}), openId: null, busy: '', loading: false, error: '', roomNames: () => ({}), linkRooms: true }
)

const emit = defineEmits<{
  (e: 'confirm', routine: Routine): void
  (e: 'pause', routine: Routine): void
  (e: 'resume', routine: Routine): void
  (e: 'run-now', routine: Routine): void
  (e: 'edit', routine: Routine): void
  (e: 'delete', routine: Routine): void
  (e: 'toggle-runs', routine: Routine): void
}>()

const drafts = computed(() => props.routines.filter((r) => r.state === 'draft'))
const others = computed(() => props.routines.filter((r) => r.state !== 'draft'))
// 删一条规则不可撤销（执行记录一起删），所以问一句再动手。问的这一条留在这里。
const confirming = ref<Routine | null>(null)

/** 手机上 ⋯ 里的「执行记录」要写的字：展开着的那一条写「收起记录」。 */
function isOpen(r: Routine): boolean {
  return props.openId === r.id
}

function confirmDelete() {
  const r = confirming.value
  confirming.value = null
  if (r) emit('delete', r)
}
</script>

<template>
  <div>
    <p v-if="error" role="alert" class="t-body c-danger mb-4">{{ error }}</p>

    <div v-if="loading && !routines.length" class="py-8 text-center" role="status" aria-label="读取规则">
      <v-progress-circular indeterminate size="28" color="primary" />
    </div>

    <template v-else>
      <section v-if="drafts.length" class="mb-6">
        <h2 class="t-section mb-2">等你确认</h2>
        <ul class="routine-list">
          <RoutineRow
            v-for="r in drafts"
            :key="r.id"
            :routine="r"
            :busy="busy"
            :room-name="roomNames[r.topic_id]"
            @confirm="emit('confirm', r)"
            @edit="emit('edit', r)"
            @delete="confirming = r"
          />
        </ul>
      </section>

      <ul v-if="others.length" class="routine-list">
        <RoutineRow
          v-for="r in others"
          :key="r.id"
          :routine="r"
          :runs="runs[r.id]"
          :open="isOpen(r)"
          :busy="busy"
          :room-name="roomNames[r.topic_id]"
          :room-to="linkRooms ? routineRoomTarget(r) : undefined"
          @pause="emit('pause', r)"
          @resume="emit('resume', r)"
          @run-now="emit('run-now', r)"
          @edit="emit('edit', r)"
          @toggle-runs="emit('toggle-runs', r)"
          @delete="confirming = r"
        />
      </ul>

      <div v-if="!routines.length && !error" class="py-8 text-center">
        <p class="t-body c-muted">还没有定时或触发规则</p>
        <p class="t-meta c-faint mt-1">点「新建」，或在房间里让芝士帮你起草一条</p>
      </div>
    </template>

    <v-dialog :model-value="!!confirming" max-width="420" @update:model-value="confirming = null">
      <v-card v-if="confirming">
        <v-card-title class="t-dialog-title">删除「{{ confirming.title }}」</v-card-title>
        <v-card-text class="t-body">删除后不再执行，执行记录也一起删除；已经放进房间的结果文件保留</v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" color="on-surface-variant" @click="confirming = null">取消</v-btn>
          <v-btn variant="text" color="error" @click="confirmDelete">删除</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<style scoped>
.routine-list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
</style>
