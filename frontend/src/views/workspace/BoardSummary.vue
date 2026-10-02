<script setup lang="ts">
// 手机上话题列表顶上那一行：看板的一句话摘要，点下去是看板。
//
// 桌面进项目落在看板上，手机进项目落在话题列表上（WorkspaceEntry），于是「整个项目
// 现在什么在等我」在手机上要多走一步才看得到。这一行把那一眼搬到列表顶上：三列各
// 几件，该你动的那一列打头、带上看板给它的那颗暖色点；其余的不抢眼。
//
// 数字和看板同一个来源（lib/projectTasks.ts 那一次读 + lib/board.ts 的
// liveBoardTasks），两边对得上。读失败就留着上一次的数（第一次就失败则只写「看
// 板」）：一次网络抖动不该让这一行说「暂无任务」。
import type { RoomTask, Topic } from '@/cx_types'

import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { t } from '@/i18n'
import { boardColumnCounts, columnDotStyle } from '@/lib/board'
import { readProjectTasks } from '@/lib/projectTasks'
import { useWorkspaceStore } from '@/stores/workspace'

const props = defineProps<{ projectId: string }>()

const router = useRouter()
const store = useWorkspaceStore()

const tasks = ref<RoomTask[] | null>(null)

/** 看板自己 15 秒一拉；这一行只是个摘要，慢一倍够了。看板也开着的时候它刚读过，
 *  这一行到点就拿它那一份（`maxAgeMs`），不另发一次。 */
const REFRESH_MS = 30_000

let inFlight = false
async function load(maxAgeMs?: number) {
  const pid = props.projectId
  if (inFlight) return
  inFlight = true
  try {
    const payload = await readProjectTasks(pid, { maxAgeMs })
    if (props.projectId === pid) tasks.value = payload.data
  } catch {
    // 留着上一次的数。
  } finally {
    inFlight = false
  }
}

let timer: number | undefined
function onVisibility() {
  if (!document.hidden) void load(REFRESH_MS)
}
onMounted(() => {
  void load()
  timer = window.setInterval(() => {
    if (!document.hidden) void load(REFRESH_MS)
  }, REFRESH_MS)
  document.addEventListener('visibilitychange', onVisibility)
})
onUnmounted(() => {
  if (timer !== undefined) window.clearInterval(timer)
  document.removeEventListener('visibilitychange', onVisibility)
})
watch(
  () => props.projectId,
  () => {
    tasks.value = null
    void load()
  }
)

const archivedRooms = computed(
  () => new Set((store.topics as Topic[]).filter((topic) => topic.status === 'archived').map((topic) => topic.id))
)
const counts = computed(() => (tasks.value ? boardColumnCounts(tasks.value, archivedRooms.value) : []))

function openBoard() {
  void router.push({ name: 'workspace-running', params: { projectId: props.projectId } })
}
</script>

<template>
  <button type="button" class="board-summary" @click="openBoard">
    <v-icon class="board-summary__glyph" size="16" icon="mdi-view-column-outline" aria-hidden="true" />
    <span class="board-summary__text">
      <template v-if="counts.length">
        <template v-for="(column, i) in counts" :key="column.key">
          <span v-if="i" class="board-summary__sep" aria-hidden="true">·</span>
          <span class="board-summary__item">
            <span
              v-if="column.key === 'needs_you'"
              class="board-summary__dot"
              :style="columnDotStyle(column.key)"
              aria-hidden="true"
            />
            {{ column.label }} {{ column.count }}
          </span>
        </template>
      </template>
      <template v-else-if="tasks">{{ t('navigation.project.board') }} · {{ t('work.room.noTasks') }}</template>
      <template v-else>{{ t('navigation.project.board') }}</template>
    </span>
    <v-icon class="board-summary__chevron" size="18" icon="mdi-chevron-right" aria-hidden="true" />
  </button>
</template>

<style scoped>
/* 和下面那几行置顶行同一套尺寸：左边 16px 的图标列、8px 的间隔，文字对齐同一列。 */
.board-summary {
  display: flex;
  align-items: center;
  gap: 8px;
  width: calc(100% - 16px);
  min-height: 44px;
  margin: 4px 8px 0;
  padding: 0 8px;
  color: var(--muted);
  font-size: 14px;
  line-height: var(--lh-14);
  text-align: start;
  background: transparent;
  border: 0;
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.board-summary:active {
  background: var(--fill-2);
}
.board-summary__glyph,
.board-summary__chevron {
  flex: none;
  color: var(--faint);
}
.board-summary__text {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.board-summary__item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.board-summary__sep {
  margin: 0 6px;
  color: var(--faint);
}
/* 看板上那颗点的样子（lib/board.ts 的 columnDotStyle 给边框和底色），小一号。 */
.board-summary__dot {
  width: 8px;
  height: 8px;
  border: 2px solid var(--faint);
  border-radius: 50%;
}
</style>
