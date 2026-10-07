<script setup lang="ts">
// 一串规则：等确认的几条摆在前面，剩下的按后端给的顺序。项目总览页（整个项目）和房间
// 右侧那一格（只这一个房间）画的是同一串，只是给进来的数组不同。
//
// 只吃 props、只往上抛动作：谁去调接口由调用方办。唯一自己做主的是「删之前问一句」——
// 那是一次确认，不是一次请求。
import type { Routine, RoutineRun } from '@/lib/routine'
import type { UserRefTarget } from '@/lib/userRef'

import { computed, ref } from 'vue'

import RoutineRow from './RoutineRow.vue'

import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import ConfirmDialog from '@/components/base/ConfirmDialog.vue'
import { t } from '@/i18n'
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
    /** 人名 → 显示名。名册查询在调用方，这一串只收结果。 */
    userNames?: Record<string, string>
  }>(),
  {
    runs: () => ({}),
    openId: null,
    busy: '',
    loading: false,
    error: '',
    roomNames: () => ({}),
    linkRooms: true,
    userNames: () => ({}),
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
  /** 点了行里的某个人名：去他的成员页 / 主页。跳路由是最外层的事。 */
  (e: 'navigate', target: UserRefTarget): void
}>()

/** 房间名：项目总览页给的名册里查不到（房间已经没了），就写「已不在的房间」；
 *  房间右侧那一格不给名册（`linkRooms` 是 false），那一格里的每一条都是这个房间的，不写。 */
function roomNameOf(r: Routine): string | undefined {
  const name = props.roomNames[r.topic_id]
  if (name) return name
  return props.linkRooms ? t('routines.goneRoom') : undefined
}

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

    <div v-if="loading && !routines.length" class="py-8 text-center" role="status" :aria-label="t('routines.loading')">
      <v-progress-circular indeterminate size="28" color="primary" />
    </div>

    <template v-else>
      <section v-if="drafts.length" class="mb-6">
        <h2 class="t-section mb-2">{{ t('routines.waiting') }}</h2>
        <ul class="routine-list">
          <RoutineRow
            v-for="r in drafts"
            :key="r.id"
            :routine="r"
            :busy="busy"
            :room-name="roomNameOf(r)"
            :user-names="userNames"
            @confirm="emit('confirm', r)"
            @edit="emit('edit', r)"
            @delete="confirming = r"
            @navigate="(t) => emit('navigate', t)"
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
          :room-name="roomNameOf(r)"
          :room-to="linkRooms ? routineRoomTarget(r) : undefined"
          :user-names="userNames"
          @pause="emit('pause', r)"
          @resume="emit('resume', r)"
          @run-now="emit('run-now', r)"
          @edit="emit('edit', r)"
          @toggle-runs="emit('toggle-runs', r)"
          @delete="confirming = r"
          @navigate="(t) => emit('navigate', t)"
        />
      </ul>

      <BaseEmptyState
        v-if="!routines.length && !error"
        size="page"
        :title="t('routines.empty')"
        :desc="t('routines.emptyHint')"
      />
    </template>

    <!-- Deleting a rule is not reversible (its run history goes too): ask before it happens. -->
    <ConfirmDialog
      :model-value="!!confirming"
      :title="t('routines.deleteTitle', { title: confirming?.title ?? '' })"
      :confirm-label="t('routines.action.delete')"
      danger
      @update:model-value="confirming = null"
      @confirm="confirmDelete"
    >
      {{ t('routines.deleteBody') }}
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
