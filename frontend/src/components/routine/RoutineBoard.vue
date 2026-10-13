<script setup lang="ts">
// 一串规则：等确认的几条摆在前面，剩下的按后端给的顺序。项目总览页（整个项目）和房间
// 右侧那一格（只这一个房间）画的是同一串，只是给进来的数组不同，以及项目页多说一句
// 「哪个频道的」。
//
// 只吃 props、只往上抛动作：谁去调接口由调用方办。唯一自己做主的是「删之前问一句」——
// 那是一次确认，不是一次请求。
import type { Routine, RoutineRun } from '@/lib/routine'

import { computed, ref } from 'vue'

import RoutineRow from './RoutineRow.vue'

import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import ConfirmDialog from '@/components/base/ConfirmDialog.vue'
import { t } from '@/i18n'
import { routineRoomTarget, routineRunsTarget } from '@/lib/routine'

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
    /** 频道名，键是频道 id。项目总览页给（那一页把整个项目的规则摆在一起，得说清每
     *  条落在哪）；房间右侧那一格不给，那一格里的每一条都是这个房间的。 */
    roomNames?: Record<string, string>
    /** 结果文件那一行是去哪儿还是当场打开。项目总览页给（这一页没有工作面板，去了频道
     *  才点得开）；房间右侧那一格不给 —— 那里点一份就当场摊开。 */
    linkFiles?: boolean
  }>(),
  {
    runs: () => ({}),
    openId: null,
    busy: '',
    loading: false,
    error: '',
    roomNames: undefined,
    linkFiles: false,
  }
)

const emit = defineEmits<{
  (e: 'confirm', routine: Routine): void
  (e: 'pause', routine: Routine): void
  (e: 'resume', routine: Routine): void
  (e: 'run-now', routine: Routine): void
  (e: 'edit', routine: Routine): void
  (e: 'delete', routine: Routine): void
  (e: 'toggle-runs', routine: Routine): void
  /** 点了某次执行交回的一份结果文件。谁来开它由调用方办。 */
  (e: 'open-file', path: string): void
}>()

/** 频道名：项目总览页的名册里查不到（频道已经没了），照实说「已不在的频道」；房间右侧
 *  那一格给不出名册，那一格里每一条都是这个房间的，不写。 */
function roomNameOf(r: Routine): string | undefined {
  if (!props.roomNames) return undefined
  return props.roomNames[r.topic_id] || t('routines.goneRoom')
}

/** 等确认的那几条摆在前面：它们此刻什么都没在跑，摆在「已经在跑」那一堆里就没人看得见，
 *  而它们是这一页唯一需要人动手的东西。 */
const ordered = computed(() => [
  ...props.routines.filter((r) => r.state === 'draft'),
  ...props.routines.filter((r) => r.state !== 'draft'),
])
// 删一条规则不可撤销（执行记录一起删），所以问一句再动手。问的这一条留在这里。
const confirming = ref<Routine | null>(null)

/** 删之前那两句里的存储位置：没填就是频道根目录。 */
const dirOf = (r: Routine | null) => (r?.output_dir ? `${r.output_dir}/` : t('routines.spec.rootDir'))

function confirmDelete() {
  const r = confirming.value
  confirming.value = null
  if (r) emit('delete', r)
}
</script>

<template>
  <div>
    <p v-if="error" role="alert" class="t-body c-danger mb-4">{{ error }}</p>

    <div v-if="loading && !routines.length" class="py-8 text-center" role="status" :aria-label="t('routines.loading')">
      <v-progress-circular indeterminate size="28" color="primary" />
    </div>

    <template v-else>
      <ul v-if="ordered.length" class="routine-list">
        <RoutineRow
          v-for="r in ordered"
          :key="r.id"
          :routine="r"
          :runs="runs[r.id]"
          :open="openId === r.id"
          :busy="busy"
          :room-name="roomNameOf(r)"
          :runs-to="routineRunsTarget(r)"
          :room-to="linkFiles ? routineRoomTarget(r) : undefined"
          @confirm="emit('confirm', r)"
          @pause="emit('pause', r)"
          @resume="emit('resume', r)"
          @run-now="emit('run-now', r)"
          @edit="emit('edit', r)"
          @toggle-runs="emit('toggle-runs', r)"
          @open-file="(path) => emit('open-file', path)"
          @delete="confirming = r"
        />
      </ul>

      <BaseEmptyState v-if="!routines.length && !error" :title="t('routines.empty')" />
    </template>

    <!-- 删掉一条规则不可撤销（执行记录跟着一起删），所以问一句再动手。 -->
    <ConfirmDialog
      :model-value="!!confirming"
      :title="t('routines.deleteTitle', { title: confirming?.title ?? '' })"
      :confirm-label="t('routines.action.delete')"
      danger
      @update:model-value="confirming = null"
      @confirm="confirmDelete"
    >
      {{ t('routines.deleteBody', { dir: dirOf(confirming) }) }}
    </ConfirmDialog>
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
