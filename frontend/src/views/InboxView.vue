<script setup lang="ts">
import { computed, onMounted } from 'vue'

import { useJoinSpace } from '@/composables/useJoinSpace'
import { useNewProjectDialog } from '@/composables/useNewProjectDialog'
import { useWaitingList } from '@/composables/useWaitingList'

import BaseButton from '@/components/base/BaseButton.vue'
import AppPage from '@/components/common/AppPage.vue'
import WaitingList from '@/components/common/Notification/WaitingList.vue'
import { t } from '@/i18n'
import { DEFAULT_SHELL, termParams } from '@/lib/shell'
import { useWorkspaceStore } from '@/stores/workspace'
import JoinSpaceDialog from '@/views/home/JoinSpaceDialog.vue'
import NotificationFeed from '@/views/home/NotificationFeed.vue'

// 「待办」：首页那一格点开就是这一页（手机上是底栏的一格）。
//
// 上面是**等你处理**（`useWaitingList` 读、`WaitingList` 画）：现在还没办完、要你动手
// 的事 —— 项目里的提问、待审阅、拍板，和等你回答的邀请、加入申请。处理完就消失，角标
// 数的就是它。
//
// 下面是**动态**（`NotificationFeed`）：发生过的事，读过就算。叫你去处理一件事的通知
// 不在动态里重复一遍，那件事在上面列着。
defineOptions({ name: 'InboxView' })

const store = useWorkspaceStore()
const waiting = useWaitingList()
onMounted(() => {
  void waiting.load()
  if (!store.projectsSettled) void store.refreshProjects()
})

// 一个项目都没有的人（刚注册）：这一页给他唯一有意义的两步。
const noProjects = computed(() => store.projectsSettled && store.projects.length === 0)
const projectTerm = termParams(DEFAULT_SHELL)
const { show: showNewProjectDialog } = useNewProjectDialog()
// 加入空间这件事和首页目录里那一行共用一份判断（见 `useJoinSpace`）：成了直接进那个空间。
const { open: joinOpen, busy: joinBusy, error: joinError, join: joinSpace } = useJoinSpace()
</script>

<template>
  <AppPage :title="t('navigation.inbox')">
    <!-- 一个项目都没有的人：新账号登录时已经有了自己的第一个项目（`landingForMember`），
         走到这里的是后来退出或删光了项目的人。三条起步路并列，每条一句话说清进去能
         得到什么。 -->
    <section v-if="noProjects" class="inbox__start">
      <p class="t-title">{{ t('work.emptyTitle', projectTerm) }}</p>
      <p class="inbox__start-lede">{{ t('work.startPaths.lede') }}</p>

      <ul class="inbox__paths">
        <li class="inbox__path">
          <v-icon icon="mdi-folder-plus-outline" size="20" class="inbox__path-icon" />
          <div class="inbox__path-text">
            <span class="inbox__path-title">{{ t('work.startPaths.project.title', projectTerm) }}</span>
            <span class="inbox__path-body">{{ t('work.startPaths.project.body') }}</span>
          </div>
          <BaseButton kind="primary" size="sm" @click="showNewProjectDialog()">
            {{ t('navigation.newProject', projectTerm) }}
          </BaseButton>
        </li>

        <li class="inbox__path">
          <v-icon icon="mdi-ticket-confirmation-outline" size="20" class="inbox__path-icon" />
          <div class="inbox__path-text">
            <span class="inbox__path-title">{{ t('work.startPaths.invite.title') }}</span>
            <span class="inbox__path-body">{{ t('work.startPaths.invite.body') }}</span>
          </div>
          <BaseButton kind="secondary" size="sm" @click="joinOpen = true">
            {{ t('work.startPaths.invite.action') }}
          </BaseButton>
        </li>

        <li class="inbox__path">
          <v-icon icon="mdi-account-group-outline" size="20" class="inbox__path-icon" />
          <div class="inbox__path-text">
            <span class="inbox__path-title">{{ t('work.startPaths.teams.title') }}</span>
            <span class="inbox__path-body">{{ t('work.startPaths.teams.body') }}</span>
          </div>
          <BaseButton kind="secondary" size="sm" :to="{ name: 'HomeTeamsExplore' }">
            {{ t('work.startPaths.teams.action') }}
          </BaseButton>
        </li>
      </ul>

      <p class="inbox__start-note">{{ t('work.startPaths.settingsNote') }}</p>
    </section>

    <WaitingList
      :items="waiting.items.value"
      :loading="waiting.loading.value"
      :failed="waiting.failed.value"
      :error-reason="waiting.errorReason.value"
      :action-error="waiting.actionError.value"
      :acting="waiting.acting.value"
      :clearing="waiting.clearing.value"
      :replying="waiting.replying.value"
      @retry="waiting.load()"
      @decide="waiting.decide"
      @dismiss="waiting.dismiss"
      @dismiss-all="waiting.dismissAll"
      @reply="waiting.reply"
      @allow="waiting.allow"
    />
    <NotificationFeed />
    <JoinSpaceDialog v-model="joinOpen" :busy="joinBusy" :error="joinError" @submit="joinSpace" />
  </AppPage>
</template>

<style scoped>
.inbox__start {
  margin-top: 24px;
  padding: 24px;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  background: var(--surface);
}
.inbox__start-lede {
  margin: 4px 0 0;
  color: var(--muted);
  font-size: 13px;
}
.inbox__paths {
  margin: 16px 0 0;
  padding: 0;
  list-style: none;
}
.inbox__path {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 0;
  border-top: 1px solid var(--line);
}
.inbox__path:first-child {
  border-top: none;
  padding-top: 0;
}
.inbox__path-icon {
  color: var(--muted);
  flex: none;
}
.inbox__path-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  flex: 1;
}
.inbox__path-title {
  color: var(--ink);
  font-size: 14px;
  font-weight: 500;
}
.inbox__path-body {
  color: var(--muted);
  font-size: 13px;
}
.inbox__start-note {
  margin: 16px 0 0;
  padding-top: 12px;
  border-top: 1px solid var(--line);
  color: var(--faint);
  font-size: 12px;
}
</style>
