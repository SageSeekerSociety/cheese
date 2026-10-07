<script setup lang="ts">
// 项目设置里的「频道」：这个项目的全部频道。项目里的每个人在这里看到每个频道是做
// 什么的、自己加入没有，加入或退出；侧栏只列加入了的，这里是找到别的频道的地方
// （侧栏频道下面那一行「浏览频道」通到这里）。管频道的人（创建者、项目管理员）在这里
// 改名、写说明、归档、取消归档。已归档的频道只在这里列出。
//
// 新建也在这里，不在侧栏上——频道少而稳定，新建是一年做几次的事；要做成的一件事建成
// 任务，不为它开频道。建好就打开它（去哪儿由页面决定）。数据就是侧栏那一份（工作区
// store）：改了什么侧栏当场跟着变，不需要再拉一次。
//
// 私密频道只有频道里的人看得到，所以这里只列出我在里面的那些，名字前面是一把锁。
// 管频道的人能把公开频道设为私密；设回公开会把全部历史给项目里所有人看，只给项目
// 管理员（和 Slack 只给工作区管理员一样）。两个方向都先确认一次。
import type { Topic } from '@/cx_types'

import { computed, ref } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import { t } from '@/i18n'
import { channelGlyph, topicTitle } from '@/lib/topicState'
import { normalizeTopicTitle, TOPIC_TITLE_MAX_LENGTH } from '@/lib/topicTitle'
import { inferTopicKind } from '@/lib/topicTree'
import { useWorkspaceStore } from '@/stores/workspace'

const props = defineProps<{
  projectId: string
  /** 我管这个项目：私密频道才有「设为公开」。页面问项目要来，后端动手时按同一条规则再判一次。 */
  canMakePublic?: boolean
}>()

const store = useWorkspaceStore()
const emit = defineEmits<{ (e: 'open-channel', topic: Topic): void }>()

const DESCRIPTION_MAX_LENGTH = 500

const mine = computed<Topic[]>(() => store.topics.filter((topic) => topic.project_id === props.projectId))
const root = computed(() => mine.value.find((topic) => inferTopicKind(topic) === 'root') ?? null)
const active = computed(() => mine.value.filter((topic) => topic.status !== 'archived' && topic.id !== root.value?.id))
const archived = computed(() => mine.value.filter((topic) => topic.status === 'archived'))

const draft = ref('')
const draftDescription = ref('')
const draftPrivate = ref(false)
const creating = ref(false)
async function create() {
  const title = draft.value.trim()
  if (!title || creating.value) return
  creating.value = true
  try {
    const topic = await store.create(title, draftDescription.value, draftPrivate.value)
    if (!topic) return
    draft.value = ''
    draftDescription.value = ''
    draftPrivate.value = false
    emit('open-channel', topic)
  } finally {
    creating.value = false
  }
}

const renamingId = ref<string | null>(null)
const renameDraft = ref('')
function startRename(topic: Topic) {
  describingId.value = null
  renamingId.value = topic.id
  renameDraft.value = topic.title
}
async function commitRename(topic: Topic) {
  if (renamingId.value !== topic.id) return
  renamingId.value = null
  const title = normalizeTopicTitle(renameDraft.value, topic.title)
  if (title) await store.renameTopic(topic.id, title)
}

const describingId = ref<string | null>(null)
const describeDraft = ref('')
function startDescribe(topic: Topic) {
  renamingId.value = null
  describingId.value = topic.id
  describeDraft.value = topic.description ?? ''
}
async function commitDescribe(topic: Topic) {
  if (describingId.value !== topic.id) return
  describingId.value = null
  const next = describeDraft.value.trim()
  if (next !== (topic.description ?? '')) await store.describe(topic.id, next)
}

// 正在确认的那一次：把哪个频道设为私密（true）还是公开（false）。
const converting = ref<{ topic: Topic; membersOnly: boolean } | null>(null)
const convertOpen = computed({
  get: () => converting.value !== null,
  set: (open: boolean) => {
    if (!open) converting.value = null
  },
})
const convertTitle = computed(() => {
  const pending = converting.value
  if (!pending) return ''
  const key = pending.membersOnly
    ? 'work.projectSettings.channels.makePrivateTitle'
    : 'work.projectSettings.channels.makePublicTitle'
  return t(key, { name: topicTitle(pending.topic) })
})
const convertingBusy = ref(false)
async function confirmConvert() {
  const pending = converting.value
  if (!pending || convertingBusy.value) return
  convertingBusy.value = true
  try {
    if (await store.setMembersOnly(pending.topic.id, pending.membersOnly)) converting.value = null
  } finally {
    convertingBusy.value = false
  }
}

// 加入、退出的那一下：按钮转圈，别的频道照常能点。
const toggling = ref<string | null>(null)
async function setJoined(topic: Topic, joined: boolean) {
  toggling.value = topic.id
  try {
    await store.setJoined(topic.id, joined)
  } finally {
    toggling.value = null
  }
}
</script>

<template>
  <div>
    <p class="t-body c-muted mb-4">{{ t('work.projectSettings.channels.intro') }}</p>

    <form class="channel-new" data-testid="channel-new" @submit.prevent="create">
      <v-text-field
        v-model="draft"
        :label="t('work.projectSettings.channels.nameLabel')"
        :maxlength="TOPIC_TITLE_MAX_LENGTH"
        density="compact"
        variant="outlined"
        hide-details
        autocomplete="off"
      />
      <v-text-field
        v-model="draftDescription"
        :label="t('work.projectSettings.channels.descriptionLabel')"
        :maxlength="DESCRIPTION_MAX_LENGTH"
        density="compact"
        variant="outlined"
        hide-details
        autocomplete="off"
      />
      <BaseButton type="submit" kind="primary" :loading="creating" :disabled="!draft.trim() || creating">
        {{ t('work.projectSettings.channels.create') }}
      </BaseButton>
      <v-checkbox
        v-model="draftPrivate"
        class="channel-new__private"
        data-testid="channel-new-private"
        :label="t('work.projectSettings.channels.privateLabel')"
        :hint="t('work.projectSettings.channels.privateHint')"
        persistent-hint
        density="compact"
      />
    </form>

    <ul class="channel-list" :aria-label="t('work.projectSettings.channels.activeLabel')">
      <li v-if="root" class="channel-row">
        <v-icon size="16" class="channel-row__glyph" icon="mdi-pound" />
        <span class="channel-row__main">
          <button type="button" class="channel-row__name" @click="emit('open-channel', root)">
            {{ topicTitle(root) }}
          </button>
          <span class="channel-row__about">{{ t('work.projectSettings.channels.rootNote') }}</span>
        </span>
      </li>
      <li v-for="topic in active" :key="topic.id" class="channel-row" :data-channel="topic.id">
        <v-icon
          size="16"
          class="channel-row__glyph"
          :icon="channelGlyph(topic)"
          :title="topic.members_only ? t('work.channel.privateTip') : undefined"
        />
        <span class="channel-row__main">
          <v-text-field
            v-if="renamingId === topic.id"
            v-model="renameDraft"
            class="channel-row__field"
            :label="t('work.projectSettings.channels.nameLabel')"
            :maxlength="TOPIC_TITLE_MAX_LENGTH"
            density="compact"
            variant="outlined"
            hide-details
            autofocus
            autocomplete="off"
            @keyup.enter="commitRename(topic)"
            @keyup.esc="renamingId = null"
            @blur="commitRename(topic)"
          />
          <button v-else type="button" class="channel-row__name" data-user-content @click="emit('open-channel', topic)">
            {{ topicTitle(topic) }}
          </button>
          <v-text-field
            v-if="describingId === topic.id"
            v-model="describeDraft"
            class="channel-row__field"
            :label="t('work.projectSettings.channels.descriptionLabel')"
            :maxlength="DESCRIPTION_MAX_LENGTH"
            density="compact"
            variant="outlined"
            hide-details
            autofocus
            autocomplete="off"
            @keyup.enter="commitDescribe(topic)"
            @keyup.esc="describingId = null"
            @blur="commitDescribe(topic)"
          />
          <span v-else-if="topic.description" class="channel-row__about" data-user-content>{{
            topic.description
          }}</span>
        </span>
        <BaseButton
          v-if="topic.joined"
          kind="ghost"
          size="sm"
          :loading="toggling === topic.id"
          @click="setJoined(topic, false)"
        >
          {{ t('work.channel.leave') }}
        </BaseButton>
        <BaseButton v-else kind="secondary" size="sm" :loading="toggling === topic.id" @click="setJoined(topic, true)">
          {{ t('work.channel.join') }}
        </BaseButton>
        <template v-if="topic.can_manage">
          <BaseButton kind="ghost" size="sm" @click="startRename(topic)">
            {{ t('work.projectSettings.channels.rename') }}
          </BaseButton>
          <BaseButton kind="ghost" size="sm" @click="startDescribe(topic)">
            {{ t('work.projectSettings.channels.describe') }}
          </BaseButton>
          <BaseButton
            v-if="!topic.members_only"
            kind="ghost"
            size="sm"
            @click="converting = { topic, membersOnly: true }"
          >
            {{ t('work.projectSettings.channels.makePrivate') }}
          </BaseButton>
          <BaseButton
            v-else-if="canMakePublic"
            kind="ghost"
            size="sm"
            @click="converting = { topic, membersOnly: false }"
          >
            {{ t('work.projectSettings.channels.makePublic') }}
          </BaseButton>
          <BaseButton kind="ghost" size="sm" @click="store.archive(topic.id)">
            {{ t('work.projectSettings.channels.archive') }}
          </BaseButton>
        </template>
      </li>
    </ul>

    <template v-if="archived.length">
      <h2 class="t-eyebrow c-muted mt-6 mb-2">{{ t('work.projectSettings.channels.archivedLabel') }}</h2>
      <ul class="channel-list" :aria-label="t('work.projectSettings.channels.archivedLabel')">
        <li v-for="topic in archived" :key="topic.id" class="channel-row" :data-channel="topic.id">
          <v-icon
            size="16"
            class="channel-row__glyph"
            :icon="channelGlyph(topic)"
            :title="topic.members_only ? t('work.channel.privateTip') : undefined"
          />
          <span class="channel-row__main">
            <button
              type="button"
              class="channel-row__name c-muted"
              data-user-content
              @click="emit('open-channel', topic)"
            >
              {{ topicTitle(topic) }}
            </button>
            <span v-if="topic.description" class="channel-row__about" data-user-content>{{ topic.description }}</span>
          </span>
          <BaseButton v-if="topic.can_manage" kind="ghost" size="sm" @click="store.unarchive(topic.id)">
            {{ t('work.projectSettings.channels.unarchive') }}
          </BaseButton>
        </li>
      </ul>
    </template>

    <AdaptiveDialog
      v-model="convertOpen"
      size="sm"
      :title="convertTitle"
      :primary-label="
        converting?.membersOnly
          ? t('work.projectSettings.channels.makePrivate')
          : t('work.projectSettings.channels.makePublic')
      "
      :primary-loading="convertingBusy"
      :close-disabled="convertingBusy"
      @primary="confirmConvert"
    >
      <p class="t-body">
        {{
          converting?.membersOnly
            ? t('work.projectSettings.channels.makePrivateBody')
            : t('work.projectSettings.channels.makePublicBody')
        }}
      </p>
    </AdaptiveDialog>
  </div>
</template>

<style scoped>
.channel-new {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-bottom: 16px;
}
.channel-new > .v-input {
  flex: 1 1 200px;
}
.channel-new > .channel-new__private {
  flex-basis: 100%;
}
/* Vuetify sets a hint's line-height to its font size, so a hint that wraps on a
   phone has its two lines touching. */
.channel-new__private :deep(.v-messages__message) {
  line-height: 1.4;
}
.channel-list {
  list-style: none;
  margin: 0;
  padding: 0;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
}
.channel-row {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 44px;
  padding: 4px 12px;
}
.channel-row + .channel-row {
  border-top: 1px solid var(--line);
}
.channel-row__glyph {
  flex: none;
  color: var(--faint);
}
.channel-row__main {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  padding-block: 4px;
}
.channel-row__name {
  max-width: 100%;
  overflow: hidden;
  padding: 0;
  border: 0;
  background: none;
  color: var(--text);
  font: inherit;
  text-align: left;
  text-overflow: ellipsis;
  white-space: nowrap;
  cursor: pointer;
}
.channel-row__name:hover {
  text-decoration: underline;
}
.channel-row__about {
  overflow: hidden;
  color: var(--muted);
  font-size: 13px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.channel-row__field {
  flex: 1 1 auto;
}
/* On a phone a row is narrower than its buttons: they wrap onto the next lines
   instead of squeezing the name to nothing. */
@media (max-width: 767.98px) {
  .channel-row {
    flex-wrap: wrap;
  }
  .channel-row__main {
    flex-basis: 180px;
  }
}
</style>
