<script setup lang="ts">
// 审核队列。规则一变，这一屏的重点就变了：
//
// - 今天「任务创建者不可自审」是一行硬检查（`tasks.py:1909`），所以管理员出了题
//   也只能找另一个管理员 —— 而板上一共可能就一个管理员，题就卡住了。
// - 新规则是**所有者与管理员都能审任何一道待审题**，包括自己出的。于是这一屏要
//   显眼地说出「这道题是你自己出的，你可以直接过」，而不是把它藏起来。
import { computed, ref, watch } from 'vue'

import PageBar from '../components/PageBar.vue'
import PanelCard from '../components/PanelCard.vue'
import { deadlineText } from '../fixtures'
import { approveTask, me, pendingTasks, rejectTask, tasks } from '../store'

/** 一页 20 道，与题目板同一口径。队列积压时（开放发题之后就一定会积压）才翻页。 */
const PAGE_SIZE = 20

const rejectFor = ref<string | null>(null)
const reason = ref('')
const page = ref(1)

const queue = computed(() =>
  [...pendingTasks.value].sort((a, b) => {
    // 自己出的排最前：能当场处理掉的先处理，队列才不会被自己堵住。
    const mineA = a.publisher.handle === me.value.handle ? 0 : 1
    const mineB = b.publisher.handle === me.value.handle ? 0 : 1
    if (mineA !== mineB) return mineA - mineB
    return new Date(a.createdAt).getTime() - new Date(b.createdAt).getTime()
  })
)

const pagedQueue = computed(() => queue.value.slice((page.value - 1) * PAGE_SIZE, page.value * PAGE_SIZE))

// 审掉一页里的题之后队列会变短：页数缩了就收回到最后一页，不要停在空页上。
watch(
  () => Math.max(1, Math.ceil(queue.value.length / PAGE_SIZE)),
  (last) => {
    if (page.value > last) page.value = last
  }
)

function setPage(next: number) {
  page.value = next
  document.querySelector('.queue')?.scrollIntoView({ block: 'start' })
}

const recentlyHandled = computed(() => tasks.value.filter((t) => t.reviewedBy && t.state !== 'PENDING').slice(0, 4))

function doApprove(id: string) {
  approveTask(id)
}

function openReject(id: string) {
  rejectFor.value = id
  reason.value = ''
}

function doReject() {
  if (!rejectFor.value || !reason.value.trim()) return
  rejectTask(rejectFor.value, reason.value.trim())
  rejectFor.value = null
}

const nowHandling = computed(() => tasks.value.find((t) => t.id === rejectFor.value))
</script>

<template>
  <div class="rev">
    <div class="rev__head">
      <div>
        <h1>审核</h1>
        <p>所有者和管理员可以审任何一道待审题，<b>包括自己出的那道</b>。不通过要写清原因，作者改完能重新提交。</p>
      </div>
      <v-chip variant="tonal" label>{{ pendingTasks.length }} 道待审</v-chip>
    </div>

    <PanelCard v-if="queue.length" title="待审核" :subtitle="`按「先审自己的、再按提交时间」排`">
      <ul class="queue">
        <li v-for="task in pagedQueue" :key="task.id" class="queue__row">
          <div class="queue__main">
            <div class="queue__titleline">
              <router-link :to="`/task/${task.id}`" class="queue__title">{{ task.title }}</router-link>
              <v-chip
                v-if="task.publisher.handle === me.handle"
                size="x-small"
                label
                variant="tonal"
                class="queue__self"
              >
                你自己出的 · 可直接通过
              </v-chip>
            </div>
            <p class="queue__summary">{{ task.summary }}</p>
            <div class="queue__meta">
              <span
                >作者 <b>{{ task.publisher.name }}</b></span
              >
              <span>{{ task.category }}</span>
              <span>{{ task.participantLimit === null ? '领取不限' : `领取上限 ${task.participantLimit}` }}</span>
              <span>{{
                task.minTeamSize === 1 && task.maxTeamSize === 1
                  ? '单人'
                  : `小队 ${task.minTeamSize}–${task.maxTeamSize}`
              }}</span>
              <span>{{ deadlineText(task) }}</span>
            </div>
          </div>
          <div class="queue__actions">
            <v-btn variant="text" size="small" :to="`/task/${task.id}`">查看</v-btn>
            <v-btn variant="tonal" size="small" color="error" @click="openReject(task.id)">驳回</v-btn>
            <v-btn variant="flat" size="small" color="primary" @click="doApprove(task.id)">通过上板</v-btn>
          </div>
        </li>
      </ul>

      <PageBar :page="page" :page-size="PAGE_SIZE" :total="queue.length" @update:page="setPage" />
    </PanelCard>

    <v-empty-state v-else icon="mdi-check-all" title="队列是空的" text="没有待审的题。有新题提交时会出现在这里。" />

    <PanelCard v-if="recentlyHandled.length" title="最近处理过" class="rev__recent">
      <ul class="recent">
        <li v-for="task in recentlyHandled" :key="task.id">
          <v-icon
            :icon="task.state === 'PUBLISHED' ? 'mdi-check-circle-outline' : 'mdi-close-circle-outline'"
            :color="task.state === 'PUBLISHED' ? 'success' : 'error'"
            size="16"
          />
          <router-link :to="`/task/${task.id}`" class="recent__title">{{ task.title }}</router-link>
          <span class="recent__by">{{ task.publisher.name }}</span>
          <span class="recent__by">{{ task.reviewedBy?.name }} 审</span>
        </li>
      </ul>
    </PanelCard>

    <v-dialog :model-value="rejectFor !== null" max-width="520" @update:model-value="rejectFor = null">
      <v-card rounded="lg" class="pa-5">
        <h3 class="text-body-1 font-weight-bold mb-2">驳回「{{ nowHandling?.title }}」</h3>
        <p class="text-body-2 text-medium-emphasis mb-3">
          原因会原样发给作者。写清「哪里不合格、改成什么样」，作者就能直接改，不用来回问。
        </p>
        <v-textarea
          v-model="reason"
          autocomplete="off"
          variant="outlined"
          rows="4"
          auto-grow
          placeholder="比如：口径没写清，先定义清楚再提交。"
        />
        <div class="d-flex justify-end ga-2 mt-4">
          <v-btn variant="text" @click="rejectFor = null">取消</v-btn>
          <v-btn color="error" variant="flat" :disabled="!reason.trim()" @click="doReject">驳回</v-btn>
        </div>
      </v-card>
    </v-dialog>
  </div>
</template>

<style scoped lang="scss">
.rev__head {
  display: flex;
  gap: 16px;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 16px;
}

.rev__head h1 {
  margin: 0;
  font-size: 1.35rem;
  font-weight: 650;
}

.rev__head p {
  max-width: 640px;
  margin: 6px 0 0;
  color: rgba(var(--v-theme-on-surface), 0.6);
  font-size: 0.84rem;
  line-height: 1.7;
}

.queue {
  padding: 0;
  margin: 0;
  list-style: none;
}

.queue__row {
  display: flex;
  gap: 16px;
  align-items: flex-start;
  justify-content: space-between;
  padding: 16px 0;
  border-top: 1px solid rgba(var(--v-theme-on-surface), 0.06);
}

.queue__row:first-child {
  border-top: none;
  padding-top: 0;
}

.queue__main {
  min-width: 0;
}

.queue__titleline {
  display: flex;
  gap: 10px;
  align-items: center;
}

.queue__title {
  font-size: 0.94rem;
  font-weight: 600;
  color: rgb(var(--v-theme-on-surface));
  text-decoration: none;
}

.queue__title:hover {
  text-decoration: underline;
}

.queue__self {
  color: rgb(var(--v-theme-primary));
}

.queue__summary {
  margin: 6px 0 8px;
  color: rgba(var(--v-theme-on-surface), 0.6);
  font-size: 0.8rem;
  line-height: 1.6;
}

.queue__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  color: rgba(var(--v-theme-on-surface), 0.5);
  font-size: 0.75rem;
}

.queue__actions {
  display: flex;
  flex: 0 0 auto;
  gap: 8px;
  align-items: center;
}

.rev__recent {
  margin-top: 16px;
}

.recent {
  padding: 0;
  margin: 0;
  list-style: none;
}

.recent li {
  display: flex;
  gap: 10px;
  align-items: center;
  padding: 8px 0;
  font-size: 0.82rem;
  border-top: 1px solid rgba(var(--v-theme-on-surface), 0.05);
}

.recent li:first-child {
  border-top: none;
}

.recent__title {
  flex: 1;
  overflow: hidden;
  color: rgb(var(--v-theme-on-surface));
  text-decoration: none;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.recent__by {
  color: rgba(var(--v-theme-on-surface), 0.5);
  font-size: 0.75rem;
}
</style>
