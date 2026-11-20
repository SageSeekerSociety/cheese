<script setup lang="ts">
// 有人在频道里单独新建了一件任务：在主线上是他发出的一条，「某某 新建了任务」下面一张
// 任务卡。同一个人接连新建的几件归在同一个名字下面（`cont`），像同一个人连着说的几句。
// 卡在原处变化（TaskCard），主线底部不再为它追加任何一行。
import type { TaskLine } from '../../lib/channelTasks'

import { isAgentHandle } from '../../lib/authorship'
import CheeseAvatar from '../CheeseAvatar.vue'
import UserAvatar from '../common/UserAvatar.vue'

import TaskCard from './TaskCard.vue'

import { t } from '@/i18n'

defineProps<{
  task: TaskLine
  /** 新建它的人；认不出来时不署名。 */
  creator: string | null
  creatorName: string
  ownerName: string | null
  /** 负责人自己挑过的头像。主线拆出去的那几张卡（ChatTimeline）是带的，这里也带上，
   *  同一件任务在主线上不该一处有脸一处没有。不传就只写名字。 */
  ownerAvatar?: string | null
  avatar: string | null
  time: string
  /** 紧接在同一个人新建的上一件后面：不再重复署名。 */
  cont?: boolean
}>()

const emit = defineEmits<{ (e: 'open', taskId: string): void }>()
</script>

<template>
  <div class="im-row task-post" :class="{ 'im-row--cont': cont }" data-testid="task-created-post">
    <div class="im-gutter">
      <button v-if="!cont && creator" type="button" class="im-person" :data-handle="creator" :aria-label="creatorName">
        <CheeseAvatar v-if="isAgentHandle(creator)" :size="28" :name="creatorName" :handle="creator" />
        <UserAvatar v-else :avatar="avatar ?? ''" :name="creatorName" :seed="creator" :size="28" />
      </button>
    </div>
    <div class="im-main">
      <div v-if="!cont" class="im-meta">
        <button v-if="creator" type="button" class="im-name im-person" :data-handle="creator">{{ creatorName }}</button>
        <span class="im-time">{{ time }}</span>
        <span class="task-post__what">{{ t('work.room.taskCard.created') }}</span>
      </div>
      <TaskCard
        :task="task"
        :owner-name="ownerName"
        :owner-avatar="task.owner ? ownerAvatar : undefined"
        @open="emit('open', $event)"
      />
    </div>
  </div>
</template>

<style scoped src="./room-row.css"></style>
<style scoped>
.task-post__what {
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}
.task-post.im-row--cont {
  margin-top: 6px;
}
</style>
