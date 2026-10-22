<script setup lang="ts">
// 支线的页头一行：它挂着的那条消息说的事，右边「转为任务」；转成以后写任务名、状态和
// 负责人，多一颗「整页打开」。桌面上画在支线那一栏顶上；手机上支线是一整页，这一行
// 就是顶栏的内容（ThreadPane 用 useScreenHeader 交给顶栏），页里不再写第二遍。
import type { ProjectMemberRow, RoomTask } from '@/cx_types'

import { computed } from 'vue'

import { getAvatarUrl } from '@/utils/materials'

import { vGuideAnchor } from '@/composables/useGuideAnchors'

import BaseButton from '@/components/base/BaseButton.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import { t } from '@/i18n'
import { memberName } from '@/lib/agentNames'
import { taskState } from '@/lib/taskState'

defineOptions({ name: 'ThreadHead' })

const props = defineProps<{
  /** 页头写的那一句：转成任务后是任务名，之前是它挂着的那条消息说的事。 */
  heading: string
  /** 这条支线转成的任务；还没转是 null。 */
  task: RoomTask | null
  /** 读这一页的人：状态那一格按他说（「等你审阅」还是「等某某审阅」）。 */
  viewer: string
  members: ProjectMemberRow[]
  memberNames: Record<string, string>
  /** 能不能转为任务：挂着的那条消息读回来了、频道没归档。 */
  canToTask: boolean
  /** 「转为任务」按下去还没回来。 */
  busy?: boolean
  /** 桌面那一栏有关闭键；手机上返回在顶栏左边。 */
  closable?: boolean
  /** 画在手机顶栏里：顶栏自己有高度和底线，这一行不再带。 */
  bar?: boolean
}>()

const emit = defineEmits<{
  (e: 'to-task'): void
  (e: 'open-page', taskId: string): void
  (e: 'close'): void
}>()

function nameOf(handle: string): string {
  return props.memberNames[handle] || handle
}
// 做这件任务的队友叫什么：状态那一句会说到它（「小 C 在做」）。任务没单独指定时是项目默认那位。
const taskAgentName = computed(() => {
  const handle = props.task?.agent_handle
  return (handle && props.memberNames[handle]) || memberName(props.members.find((m) => m.agent && m.project_default))
})
// 这个人的头像图：从成员名册查他挑过的素材 id。名册上没有他、或者他从没挑过
// （avatar_id 是 null）都给空串，让 UserAvatar 画彩色首字母——别再退回全站默认脸。
function avatarOf(handle: string): string {
  return getAvatarUrl(props.members.find((m) => m.user_handle === handle)?.avatar_id)
}
</script>

<template>
  <!-- One row, each thing in its own cell rather than strung into a sentence with dots. In the
       phone bar the title takes the whole first line and the state and owner go under it. -->
  <header v-guide-anchor="task ? 'task-pane' : ''" class="thread-head" :class="{ 'thread-head--bar': bar }">
    <v-icon v-if="task && !bar" size="18" class="thread-head__icon" aria-hidden="true"
      >mdi-checkbox-marked-outline</v-icon
    >
    <div class="thread-head__text">
      <h2 class="thread-head__title t-title" :title="heading">{{ heading }}</h2>
      <div v-if="task" class="thread-head__meta">
        <span class="thread-head__state" data-testid="thread-task-phrase">{{
          taskState(task, { viewer, agentName: taskAgentName }).label
        }}</span>
        <span v-if="bar && task.owner_handle" class="thread-head__owner">
          <UserAvatar :size="14" :name="nameOf(task.owner_handle)" :avatar="avatarOf(task.owner_handle)" />
          <span class="thread-head__owner-name">{{
            t('work.task.ownerChip', { name: nameOf(task.owner_handle) })
          }}</span>
        </span>
      </div>
    </div>
    <template v-if="task">
      <template v-if="!bar">
        <span class="thread-head__gap" />
        <span v-if="task.owner_handle" class="thread-head__owner">
          <UserAvatar :size="20" :name="nameOf(task.owner_handle)" :avatar="avatarOf(task.owner_handle)" />
          <span class="thread-head__owner-name">{{
            t('work.task.ownerChip', { name: nameOf(task.owner_handle) })
          }}</span>
        </span>
      </template>
      <BaseButton
        v-guide-anchor="'task-open-page'"
        icon="mdi-arrow-expand"
        size="sm"
        :title="t('work.room.thread.openPage')"
        :aria-label="t('work.room.thread.openPage')"
        data-testid="thread-open-page"
        @click="emit('open-page', task.id)"
      />
    </template>
    <template v-else>
      <span class="thread-head__gap" />
      <BaseButton
        v-if="canToTask"
        kind="secondary"
        size="sm"
        :loading="busy"
        data-testid="thread-to-task"
        @click="emit('to-task')"
        >{{ t('work.room.thread.toTask') }}</BaseButton
      >
    </template>
    <BaseButton
      v-if="closable"
      icon="mdi-close"
      size="sm"
      :title="t('work.room.thread.close')"
      :aria-label="t('work.room.thread.close')"
      @click="emit('close')"
    />
  </header>
</template>

<style scoped>
.thread-head {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 48px;
  padding: 0 12px 0 16px;
  border-bottom: 1px solid var(--line);
}
/* 顶栏里：高度、底线、左右的边距都是顶栏的（← 后面那一格，右边贴着顶栏的内边距）。 */
.thread-head--bar {
  flex: 1 1 0;
  min-width: 0;
  height: 100%;
  min-height: 0;
  padding: 0 4px 0 0;
  border-bottom: 0;
}
.thread-head__icon {
  flex: none;
  color: var(--muted);
}
.thread-head__text,
.thread-head__meta {
  display: flex;
  flex: 0 1 auto;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.thread-head__meta {
  flex: none;
}
.thread-head__title {
  flex: 0 1 auto;
  min-width: 0;
  margin: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.thread-head__gap {
  flex: 1 1 0;
}
/* 和任务页页头那一格同一个样子：同一件任务，两处写法一样。 */
.thread-head__state {
  display: inline-flex;
  flex: none;
  align-items: center;
  padding: 1px 8px;
  border-radius: var(--radius-sm);
  background: var(--fill);
  color: var(--muted);
  font-size: 12px;
  font-weight: 600;
}
.thread-head__owner {
  display: inline-flex;
  flex: none;
  align-items: center;
  gap: 6px;
  padding: 0 4px;
  color: var(--text);
  font-size: 13px;
}
.thread-head__owner-name {
  white-space: nowrap;
}

/* 手机顶栏里两行：任务名独占第一行，状态和负责人退到第二行，同一号字、同一种灰，
   各成一格，靠间距和负责人的小头像分开（不拿点号串）。 */
.thread-head--bar .thread-head__text {
  flex: 1 1 auto;
  flex-direction: column;
  align-items: flex-start;
  justify-content: center;
  gap: 1px;
}
.thread-head--bar .thread-head__title {
  max-width: 100%;
}
.thread-head--bar .thread-head__meta {
  gap: 16px;
  max-width: 100%;
}
.thread-head--bar .thread-head__state,
.thread-head--bar .thread-head__owner {
  padding: 0;
  background: none;
  color: var(--muted);
  font-size: 13px;
  font-weight: 400;
  line-height: var(--lh-13);
}
.thread-head--bar .thread-head__owner {
  gap: 4px;
  min-width: 0;
  flex: 0 1 auto;
}
.thread-head--bar .thread-head__owner-name {
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
