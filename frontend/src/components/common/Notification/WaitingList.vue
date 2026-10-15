<template>
  <!-- 等你处理: what still waits on the person, gone once handled. The backend computes
       it from current state (`/awaiting-me`), so this lists what the badge counts. Work
       groups by project; invitations and join requests by the project or team to join,
       answered on the row. -->
  <section class="waiting">
    <h2 class="waiting__heading">
      {{ t('home.inbox.waiting') }}
      <span v-if="!loading && !failed && items.length" class="waiting__total t-meta-read">{{ items.length }}</span>
    </h2>
    <LoadingSkeleton v-if="loading" variant="list" class="waiting__card waiting__skel" />
    <!-- Items waiting on you failed to load: replace this block in place with an
         error and a retry (docs/design-system.md §3.10). -->
    <BaseLoadError
      v-else-if="failed"
      class="waiting__load-error"
      :title="t('home.inbox.loadFailed')"
      :error="errorReason || null"
      @retry="emit('retry')"
    />
    <p v-else-if="items.length === 0" class="waiting__card waiting__quiet t-body">
      {{ t('home.inbox.waitingEmpty') }}
    </p>
    <template v-else>
      <p v-if="actionError" role="alert" class="waiting__error t-meta">{{ actionError }}</p>
      <div class="waiting__card">
        <section v-for="group in groups" :key="group.key" class="waiting__section">
          <div class="waiting__group">
            <h3 class="waiting__group-name">
              <span data-user-content>{{ group.name }}</span>
              <span class="waiting__count t-meta-read">{{ group.items.length }}</span>
            </h3>
            <BaseButton
              v-if="group.projectId && unreadIn(group) > 1"
              size="sm"
              :loading="clearing === group.projectId"
              @click="emit('dismissAll', group.projectId)"
            >
              {{ t('home.inbox.markAllRead') }}
            </BaseButton>
          </div>

          <template v-for="item in group.items" :key="keyOf(item)">
            <!-- Work in a project: what you are asked to do, then which thing, then what it waits on. -->
            <InboxRow v-if="item.kind === 'work'" :to="linkTo(item)" :time="relTime(item.at)" :at="item.at">
              <template #lead>
                <v-icon :icon="iconOf(item)" size="16" />
              </template>
              <template #title>
                <span class="waiting__ask" :class="`waiting__ask--${askOf(item).tone}`">{{ askOf(item).text }}</span>
                <span :data-user-content="itemTitleWritten(item) || undefined">{{ itemTitle(item) }}</span>
              </template>
              <span v-if="item.detail" class="waiting__detail t-body" data-user-content>{{ item.detail }}</span>
              <span v-if="item.topicId" class="waiting__where" :data-user-content="item.topicTitle || undefined">
                # {{ topicTitle({ title: item.topicTitle }) }}
              </span>
              <template v-if="item.reason === 'decide' || item.reason === 'read'" #below>
                <div v-if="item.reason === 'decide' && item.options?.length" class="waiting__options">
                  <BaseButton
                    v-for="option in item.options"
                    :key="option"
                    size="sm"
                    kind="secondary"
                    :loading="acting === item.alertId"
                    @click="emit('decide', item, option)"
                  >
                    {{ option }}
                  </BaseButton>
                </div>
                <!-- A decision request without options (old rows only): answering means
                     writing a sentence. Without this path the row could never be cleared. -->
                <div v-else-if="item.reason === 'decide' && item.alertId != null" class="waiting__options">
                  <v-text-field
                    v-model="answers[item.alertId]"
                    variant="outlined"
                    density="compact"
                    hide-details
                    autocomplete="off"
                    :aria-label="t('home.inbox.answerLabel')"
                    :placeholder="t('home.inbox.answerLabel')"
                    class="waiting__answer"
                    @keydown.enter.prevent="answer(item)"
                  />
                  <BaseButton
                    size="sm"
                    kind="secondary"
                    :disabled="!answers[item.alertId]?.trim()"
                    :loading="acting === item.alertId"
                    @click="answer(item)"
                  >
                    {{ t('home.inbox.answer') }}
                  </BaseButton>
                </div>
                <div v-else-if="item.reason === 'read'" class="waiting__options">
                  <BaseButton
                    size="sm"
                    kind="secondary"
                    :loading="acting === item.alertId"
                    @click="emit('dismiss', item)"
                  >
                    {{ t('home.inbox.markRead') }}
                  </BaseButton>
                </div>
              </template>
            </InboxRow>

            <!-- An invitation or join request: who sent it, the two answers always shown.
                 No mark-read / delete: answering is how it leaves. -->
            <InboxRow v-else :time="relTime(item.at)" :at="item.at">
              <template #lead>
                <UserAvatar
                  :size="20"
                  :avatar="item.person.avatarUrl ?? ''"
                  :name="item.person.name"
                  :seed="item.person.handle || item.person.name"
                />
              </template>
              <template #title>
                <span class="waiting__ask waiting__ask--warn">{{ askLabelOf(item) }}</span>
                <span data-user-content>{{ askTitleOf(item) }}</span>
              </template>
              <span v-if="item.message" class="waiting__detail t-body" data-user-content>{{ item.message }}</span>
              <span v-if="item.role && item.role !== 'MEMBER'" class="waiting__where">
                {{ t('home.inbox.invite.asRole', { role: t(`home.inbox.role.${item.role}`) }) }}
              </span>
              <template #side>
                <BaseButton
                  size="sm"
                  kind="secondary"
                  :loading="replying === keyOf(item)"
                  @click="emit('reply', item, true)"
                >
                  {{ item.kind === 'join_request' ? t('home.inbox.invite.approve') : t('home.inbox.invite.accept') }}
                </BaseButton>
                <BaseButton size="sm" :disabled="replying === keyOf(item)" @click="emit('reply', item, false)">
                  {{ t('home.inbox.invite.decline') }}
                </BaseButton>
              </template>
            </InboxRow>
          </template>
        </section>
      </div>
    </template>
  </section>
</template>

<script setup lang="ts">
import type { WaitingAsk, WaitingItem, WaitingWork } from '@/cx_types'

import { computed, ref } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import LoadingSkeleton from '@/components/common/LoadingSkeleton.vue'
import InboxRow from '@/components/common/Notification/InboxRow.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import { t } from '@/i18n'
import { relTime } from '@/lib/relTime'
import { taskTitle, topicTitle } from '@/lib/topicState'
import { waitingKey as keyOf } from '@/lib/waitingKey'

const props = defineProps<{
  items: WaitingItem[]
  loading: boolean
  failed: boolean
  errorReason: string
  /** 刚才那一下没交上去时的话；没有就空。 */
  actionError: string
  /** 正在交的那一条拍板或已读（通知号）。 */
  acting: number | null
  /** 正在「全部标为已读」的那个项目。 */
  clearing: string | null
  /** 正在回答的那一条邀请或申请（`waitingKey`）。 */
  replying: string | null
}>()

const emit = defineEmits<{
  retry: []
  decide: [item: WaitingWork, chosen: string]
  dismiss: [item: WaitingWork]
  dismissAll: [projectId: string]
  reply: [item: WaitingAsk, yes: boolean]
}>()

/** 这一件要你做什么。负责人那一条按停在哪一格说：检查没过，或被退回。 */
function askOf(item: WaitingWork): { text: string; tone: 'warn' | 'danger' } {
  if (item.reason === 'reviewer') return { text: t('home.inbox.reason.reviewer'), tone: 'warn' }
  if (item.reason === 'reporter') return { text: t('home.inbox.reason.reporter'), tone: 'warn' }
  if (item.reason === 'asked') return { text: t('home.inbox.reason.asked'), tone: 'warn' }
  if (item.reason === 'decide') return { text: t('home.inbox.reason.decide'), tone: 'warn' }
  if (item.reason === 'read') return { text: t('home.inbox.reason.read'), tone: 'warn' }
  if (item.phrase === 'checks_failed') return { text: t('home.inbox.reason.checksFailed'), tone: 'danger' }
  return { text: t('home.inbox.reason.bounced'), tone: 'warn' }
}

/** 左边那个小图标：一眼分出是哪一类事。 */
function iconOf(item: WaitingWork): string {
  switch (item.reason) {
    case 'reviewer':
      return 'mdi-file-check-outline'
    case 'reporter':
      return 'mdi-package-variant-closed-check'
    case 'asked':
      return 'mdi-comment-question-outline'
    case 'decide':
      return 'mdi-source-fork'
    case 'read':
      return 'mdi-text-box-outline'
    default:
      return item.phrase === 'checks_failed' ? 'mdi-close-circle-outline' : 'mdi-undo-variant'
  }
}

/** 任务的事写任务的名字，频道自己的事写频道的；来自通知的写通知的标题。 */
function itemTitle(item: WaitingWork): string {
  if (item.alertId != null && item.headline) return item.headline
  if (item.taskTitle) return taskTitle({ title: item.taskTitle, title_source: item.taskTitleSource })
  return topicTitle({ title: item.topicTitle })
}

/** 标题里人写的那一段：没起名时那一行写的是界面的「新任务」，不算。 */
function itemTitleWritten(item: WaitingWork): string {
  if (item.alertId != null && item.headline) return item.headline
  return item.taskTitle ?? item.topicTitle
}

function linkTo(item: WaitingWork) {
  const query = item.blockId ? { block: item.blockId } : undefined
  // 任务的事打开任务页，不是它所在的频道。
  if (item.taskId) {
    return {
      name: 'workspace-task',
      params: { projectId: item.projectId, topicId: item.topicId, taskId: item.taskId },
      query,
    }
  }
  // 芝士在支线里问的，打开那条支线。
  if (item.threadId) {
    return {
      name: 'workspace-thread',
      params: { projectId: item.projectId, topicId: item.topicId, threadId: item.threadId },
      query,
    }
  }
  // 不指向哪个频道的通知，打开它所在项目的总览。
  if (!item.topicId) return { name: 'workspace-overview', params: { projectId: item.projectId } }
  return { name: 'workspace-topic', params: { projectId: item.projectId, topicId: item.topicId }, query }
}

function askLabelOf(item: WaitingAsk): string {
  return item.kind === 'join_request' ? t('home.inbox.reason.joinRequest') : t('home.inbox.reason.invited')
}

function askTitleOf(item: WaitingAsk): string {
  const person = item.person.name
  if (item.kind === 'project_invite') return t('home.inbox.invite.project', { person })
  if (item.kind === 'team_invite') return t('home.inbox.invite.team', { person })
  return t('home.inbox.invite.request', { person })
}

interface Group {
  key: string
  name: string
  /** 项目那一组才有：「全部标为已读」按项目清。 */
  projectId: string | null
  items: WaitingItem[]
}

const placeKindOf = (item: WaitingAsk) => (item.kind === 'project_invite' ? 'project' : 'team')

// 按要处理的地方分组：项目里的事按项目，邀请和申请按要加入的项目或团队。组的先后
// 跟着组里最近那一件走（清单本来就是最近的在前）。
const groups = computed(() => {
  const out: Group[] = []
  for (const item of props.items) {
    const key = item.kind === 'work' ? `project:${item.projectId}` : `${placeKindOf(item)}:${item.placeId}`
    const group = out.find((g) => g.key === key)
    if (group) group.items.push(item)
    else
      out.push({
        key,
        name: item.kind === 'work' ? item.projectName : item.place.name,
        projectId: item.kind === 'work' ? item.projectId : null,
        items: [item],
      })
  }
  return out
})

// 一个项目里攒了好几条变更提醒时，组标题上给一下全部标为已读。
const unreadIn = (group: Group) => group.items.filter((item) => item.kind === 'work' && item.reason === 'read').length

// 没带选项的决策请求：写一句话答复。写了一半的字留在这一格里，交上去才发出去。
const answers = ref<Record<number, string>>({})
function answer(item: WaitingWork) {
  const text = item.alertId == null ? '' : (answers.value[item.alertId] ?? '').trim()
  if (text) emit('decide', item, text)
}
</script>

<style scoped>
.waiting__heading {
  display: flex;
  gap: 8px;
  align-items: baseline;
  margin: 24px 0 8px;
  font-size: 14px;
  font-weight: 600;
  color: var(--ink);
}
.waiting__total {
  font-weight: 400;
}
.waiting__card {
  margin: 0;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  background: var(--surface);
  overflow: hidden;
}
.waiting__skel {
  padding: 8px 12px;
}
.waiting__quiet {
  display: flex;
  align-items: center;
  min-height: 44px;
  padding: 0 16px;
  color: var(--muted);
}
.waiting__load-error {
  padding: 8px 0;
}
.waiting__error {
  margin: 0 0 8px;
  color: var(--danger-ink);
}
/* 一组的小标题：项目或团队的名字和这一组的件数。紧跟在它下面的那一行不再画顶上那条线。 */
.waiting__group {
  display: flex;
  gap: 8px;
  align-items: center;
  justify-content: space-between;
  min-height: 32px;
  padding: 8px 16px 0;
}
.waiting__group + :deep(.inbox-row) {
  border-top: none;
}
.waiting__group-name {
  display: flex;
  gap: 8px;
  align-items: baseline;
  margin: 0;
  color: var(--muted);
  font-size: 12px;
  font-weight: 600;
  line-height: var(--lh-12);
}
.waiting__count {
  font-weight: 400;
}
.waiting__ask {
  margin-right: 8px;
  font-weight: 600;
}
.waiting__ask--warn {
  color: var(--accent-ink);
}
.waiting__ask--danger {
  color: var(--danger-ink);
}
.waiting__detail {
  display: -webkit-box;
  overflow: hidden;
  color: var(--text);
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}
.waiting__where {
  color: var(--muted);
  font-size: 12px;
  line-height: var(--lh-12);
}
.waiting__options {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}
.waiting__answer {
  flex: 1;
  min-width: 0;
}
</style>
