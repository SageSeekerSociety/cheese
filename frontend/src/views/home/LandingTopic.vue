<script setup lang="ts">
import { computed } from 'vue'

import { avatarColor, avatarInitial } from '@/utils/avatar'

import CheeseAvatar from '@/components/CheeseAvatar.vue'
import { t } from '@/i18n'

// One topic as the public site shows it: who posted it, which course took it
// up, and how each group working on it is doing. Sample data, drawn with the
// product's own avatars.

const people = computed(() => t('publicSite.topicCard.members').split(','))

const groups = computed(() => [
  {
    name: t('publicSite.topicCard.group1'),
    members: people.value.slice(0, 3),
    state: t('publicSite.topicCard.state1'),
    tone: 'review',
    progress: 90,
  },
  {
    name: t('publicSite.topicCard.group2'),
    members: people.value.slice(3, 5),
    state: t('publicSite.topicCard.state2'),
    tone: 'work',
    progress: 60,
  },
  {
    name: t('publicSite.topicCard.group3'),
    members: people.value.slice(5, 8),
    state: t('publicSite.topicCard.state3'),
    tone: 'work',
    progress: 35,
  },
])
</script>

<template>
  <div class="topic" inert>
    <div class="topic-top">
      <span class="topic-kind">{{ t('publicSite.topicCard.kind') }}</span>
      <span class="topic-sample">{{ t('publicSite.room.sample') }}</span>
    </div>
    <h3 class="topic-title">{{ t('publicSite.topicCard.title') }}</h3>
    <dl class="topic-meta">
      <div>
        <dt>{{ t('publicSite.topicCard.postedBy') }}</dt>
        <dd>{{ t('publicSite.topicCard.company') }}</dd>
      </div>
      <div>
        <dt>{{ t('publicSite.topicCard.course') }}</dt>
        <dd>{{ t('publicSite.topicCard.courseName') }}</dd>
      </div>
      <div>
        <dt>{{ t('publicSite.topicCard.mentor') }}</dt>
        <dd>{{ t('publicSite.room.li') }}</dd>
      </div>
    </dl>
    <ul class="topic-groups">
      <li v-for="group in groups" :key="group.name" class="topic-group">
        <span class="topic-group-name">{{ group.name }}</span>
        <span class="topic-avatars">
          <span
            v-for="member in group.members"
            :key="member"
            class="topic-avatar"
            :style="{ backgroundColor: avatarColor(member) }"
            >{{ avatarInitial(member) }}</span
          >
          <CheeseAvatar :size="24" :name="t('publicSite.room.agent')" class="topic-avatar-ai" />
        </span>
        <span class="topic-bar"><span :style="{ width: `${group.progress}%` }" /></span>
        <span class="topic-state" :class="{ 'topic-state-review': group.tone === 'review' }">{{ group.state }}</span>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.topic {
  display: flex;
  padding: 32px;
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-lg);
  flex-direction: column;
  gap: 16px;
}

.topic-top {
  display: flex;
  justify-content: space-between;
}

.topic-kind {
  font-size: 13px;
  font-weight: 600;
  line-height: var(--lh-13);
  color: var(--muted);
}

.topic-sample {
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--faint);
}

.topic-title {
  font-size: 23px;
  font-weight: 600;
  line-height: var(--lh-23);
  color: var(--ink);
}

.topic-meta {
  display: grid;
  padding-bottom: 16px;
  margin: 0;
  border-bottom: 1px solid var(--line);
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
}

.topic-meta dt {
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
}

.topic-meta dd {
  margin: 4px 0 0;
  font-size: 14px;
  line-height: var(--lh-14);
  color: var(--ink);
}

.topic-groups {
  display: flex;
  padding: 0;
  margin: 0;
  list-style: none;
  flex-direction: column;
  gap: 16px;
}

.topic-group {
  display: grid;
  grid-template-columns: 64px 104px minmax(0, 1fr) 120px;
  align-items: center;
  gap: 16px;
}

.topic-group-name {
  font-size: 14px;
  line-height: var(--lh-14);
  color: var(--text);
}

.topic-avatars {
  display: flex;
}

.topic-avatar,
.topic-avatar-ai {
  margin-left: -6px;
  outline: 2px solid var(--surface);
}

.topic-avatar {
  display: flex;
  width: 24px;
  height: 24px;
  font-size: 12px;
  font-weight: 600;
  color: var(--inverse-ink);
  border-radius: var(--radius-md);
  align-items: center;
  justify-content: center;
}

.topic-avatar:first-child {
  margin-left: 0;
}

.topic-bar {
  height: 4px;
  overflow: hidden;
  background: var(--fill-2);
  border-radius: var(--radius-pill);
}

.topic-bar span {
  display: block;
  height: 100%;
  background: var(--ink);
  border-radius: var(--radius-pill);
}

.topic-state {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
  text-align: right;
}

.topic-state-review {
  color: var(--warn-ink);
}
</style>
