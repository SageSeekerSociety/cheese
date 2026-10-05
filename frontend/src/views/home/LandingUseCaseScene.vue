<script lang="ts">
export type UseCaseRole = 'teachers' | 'students' | 'office' | 'developers'
</script>

<script setup lang="ts">
import { computed } from 'vue'

import { avatarColor, avatarInitial } from '@/utils/avatar'

import CheeseAvatar from '@/components/CheeseAvatar.vue'
import { t } from '@/i18n'

// The screen each use case happens on, drawn small: sample data in the product's
// own words (领取者, 待你审阅, 看板) so what a visitor sees here is what they will
// find after signing in. Decorative, so it is inert and hidden from screen readers.

defineProps<{ role: UseCaseRole }>()

const files = computed(() => [
  t('publicSite.useCases.scene.file1'),
  t('publicSite.useCases.scene.file2'),
  t('publicSite.useCases.scene.file3'),
])
</script>

<template>
  <div class="scene" inert aria-hidden="true">
    <div class="scene-top">
      <span class="scene-kind">
        <template v-if="role === 'teachers'">{{ t('publicSite.useCases.scene.roster') }}</template>
        <template v-else-if="role === 'students'">{{ t('publicSite.useCases.scene.challenge') }}</template>
        <template v-else-if="role === 'office'">{{ t('publicSite.useCases.scene.topic') }}</template>
        <template v-else>{{ t('publicSite.useCases.scene.board') }}</template>
      </span>
      <span class="scene-sample">{{ t('publicSite.room.sample') }}</span>
    </div>

    <template v-if="role === 'teachers'">
      <p class="scene-title">{{ t('publicSite.useCases.scene.assignment') }}</p>
      <ul class="scene-rows">
        <li class="scene-row">
          <span class="scene-who">
            <span class="scene-avatar" :style="{ backgroundColor: avatarColor('wang') }">{{
              avatarInitial(t('publicSite.useCases.scene.wang'))
            }}</span>
            {{ t('publicSite.useCases.scene.wang') }}
          </span>
          <span class="scene-state">{{ t('publicSite.useCases.scene.claimPending') }}</span>
          <span class="scene-actions">
            <span class="scene-button">{{ t('publicSite.useCases.scene.reject') }}</span>
            <span class="scene-button">{{ t('publicSite.useCases.scene.approve') }}</span>
          </span>
        </li>
        <li class="scene-row">
          <span class="scene-who">
            <span class="scene-avatar" :style="{ backgroundColor: avatarColor('chen') }">{{
              avatarInitial(t('publicSite.useCases.scene.chen'))
            }}</span>
            {{ t('publicSite.useCases.scene.chen') }}
          </span>
          <span class="scene-state">{{ t('publicSite.useCases.scene.reviewPending') }}</span>
          <span class="scene-actions">
            <span class="scene-button">{{ t('publicSite.useCases.scene.review') }}</span>
          </span>
        </li>
        <li class="scene-row">
          <span class="scene-who">
            <span class="scene-avatar" :style="{ backgroundColor: avatarColor('zhou') }">{{
              avatarInitial(t('publicSite.useCases.scene.zhou'))
            }}</span>
            {{ t('publicSite.useCases.scene.zhou') }}
          </span>
          <span class="scene-state scene-state-done">{{ t('publicSite.useCases.scene.passed') }}</span>
          <span class="scene-actions" />
        </li>
      </ul>
    </template>

    <template v-else-if="role === 'students'">
      <p class="scene-title">{{ t('publicSite.useCases.scene.assignment') }}</p>
      <p class="scene-meta">{{ t('publicSite.useCases.scene.assignmentMeta') }}</p>
      <div class="scene-chat">
        <p class="scene-line">
          <span class="scene-avatar" :style="{ backgroundColor: avatarColor('wang') }">{{
            avatarInitial(t('publicSite.useCases.scene.wang'))
          }}</span>
          <span>{{ t('publicSite.useCases.scene.studentAsk') }}</span>
        </p>
        <p class="scene-line">
          <CheeseAvatar :size="24" :name="t('publicSite.room.agent')" handle="cheese" />
          <span>{{ t('publicSite.useCases.scene.studentAnswer') }}</span>
        </p>
      </div>
      <div class="scene-card">
        <span class="scene-card-text">
          <span class="scene-state scene-state-done">{{ t('publicSite.useCases.scene.passed') }}</span>
          {{ t('publicSite.useCases.scene.reviewNote') }}
        </span>
        <span class="scene-button">{{ t('publicSite.useCases.scene.submitAgain') }}</span>
      </div>
    </template>

    <template v-else-if="role === 'office'">
      <div class="scene-chat">
        <div class="scene-line">
          <span class="scene-avatar" :style="{ backgroundColor: avatarColor('zhao') }">{{
            avatarInitial(t('publicSite.useCases.scene.zhao'))
          }}</span>
          <div class="scene-message">
            <span>{{ t('publicSite.useCases.scene.officeAsk') }}</span>
            <span class="scene-files">
              <span v-for="file in files" :key="file" class="scene-file">
                <v-icon icon="mdi-file-document-outline" size="14" />{{ file }}
              </span>
            </span>
          </div>
        </div>
        <p class="scene-line">
          <span class="scene-avatar" :style="{ backgroundColor: avatarColor('zhao') }">{{
            avatarInitial(t('publicSite.useCases.scene.zhao'))
          }}</span>
          <span>{{ t('publicSite.useCases.scene.officeAdd') }}</span>
        </p>
      </div>
      <div class="scene-card">
        <span class="scene-card-text">
          {{ t('publicSite.useCases.scene.report') }}
          <span class="scene-state scene-state-wait">{{ t('publicSite.useCases.scene.waitingOnYou') }}</span>
        </span>
        <span class="scene-actions">
          <span class="scene-button">{{ t('publicSite.useCases.scene.sendBack') }}</span>
          <span class="scene-button">{{ t('publicSite.useCases.scene.accept') }}</span>
        </span>
      </div>
    </template>

    <template v-else>
      <ul class="scene-rows">
        <li class="scene-row">
          <span class="scene-task">{{ t('publicSite.useCases.scene.task1') }}</span>
          <span class="scene-state scene-state-wait">{{ t('publicSite.useCases.scene.waitingOnYou') }}</span>
          <span class="scene-pr">{{ t('publicSite.useCases.scene.pr') }}</span>
        </li>
        <li class="scene-row">
          <span class="scene-task">{{ t('publicSite.useCases.scene.task2') }}</span>
          <span class="scene-state">{{ t('publicSite.useCases.scene.inProgress') }}</span>
          <CheeseAvatar :size="20" :name="t('publicSite.room.agent')" handle="cheese" />
        </li>
        <li class="scene-row">
          <span class="scene-task">{{ t('publicSite.useCases.scene.task3') }}</span>
          <span class="scene-state">{{ t('publicSite.useCases.scene.inProgress') }}</span>
          <CheeseAvatar :size="20" :name="t('publicSite.room.agent')" handle="cheese" />
        </li>
      </ul>
      <p class="scene-meta">{{ t('publicSite.useCases.scene.mergeRule') }}</p>
    </template>
  </div>
</template>

<style scoped>
.scene {
  display: flex;
  padding: 24px;
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-lg);
  flex-direction: column;
  gap: 16px;
}

.scene-top {
  display: flex;
  justify-content: space-between;
}

.scene-kind {
  font-size: 13px;
  font-weight: 600;
  line-height: var(--lh-13);
  color: var(--muted);
}

.scene-sample {
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--faint);
}

.scene-title {
  font-size: 18px;
  font-weight: 600;
  line-height: var(--lh-18);
  color: var(--ink);
}

.scene-meta {
  margin-top: -8px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}

.scene-rows {
  display: flex;
  padding: 0;
  margin: 0;
  list-style: none;
  flex-direction: column;
}

.scene-row {
  display: grid;
  min-height: 48px;
  padding: 8px 0;
  border-top: 1px solid var(--line);
  grid-template-columns: minmax(0, 1fr) auto auto;
  align-items: center;
  gap: 16px;
}

.scene-who,
.scene-line {
  display: flex;
  font-size: 14px;
  line-height: var(--lh-14);
  color: var(--text);
  align-items: center;
  gap: 8px;
}

.scene-line {
  align-items: flex-start;
}

.scene-task {
  overflow: hidden;
  font-size: 14px;
  line-height: var(--lh-14);
  color: var(--ink);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.scene-avatar {
  display: flex;
  width: 24px;
  height: 24px;
  font-size: 12px;
  font-weight: 600;
  color: var(--inverse-ink);
  border-radius: var(--radius-pill);
  flex-shrink: 0;
  align-items: center;
  justify-content: center;
}

.scene-state {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
  white-space: nowrap;
}

.scene-state-wait {
  color: var(--warn-ink);
}

.scene-state-done {
  color: var(--ok-ink);
}

.scene-actions {
  display: flex;
  gap: 8px;
}

.scene-button {
  padding: 4px 12px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--ink);
  white-space: nowrap;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
}

.scene-pr {
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
}

.scene-chat {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.scene-message {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.scene-files {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.scene-file {
  display: inline-flex;
  padding: 4px 8px;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
  background: var(--fill);
  border-radius: var(--radius-sm);
  align-items: center;
  gap: 4px;
}

.scene-card {
  display: flex;
  padding: 12px 16px;
  background: var(--fill);
  border-radius: var(--radius-md);
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.scene-card-text {
  display: flex;
  font-size: 14px;
  line-height: var(--lh-14);
  color: var(--ink);
  flex-wrap: wrap;
  align-items: baseline;
  gap: 8px;
}

/* A phone has no room for name, state and buttons on one line: the state moves under the name. */
@media (width <= 520px) {
  .scene-row {
    grid-template-columns: minmax(0, 1fr) auto;
    row-gap: 4px;
  }

  .scene-row > .scene-state {
    grid-row: 2;
    grid-column: 1;
  }

  .scene-row > :last-child {
    grid-row: 1 / span 2;
    grid-column: 2;
  }
}
</style>
