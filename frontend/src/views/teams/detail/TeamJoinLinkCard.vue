<script setup lang="ts">
// 团队设置「成员」那一栏的「加入方式」：团队链接（长期有效，可重置）、加入要不要审批、
// 搜不搜得到。团队地址在「基本信息」那一栏。
//
// 这一件只吃数据、往上报动作：几样东西取没取回来、写成了没有，都是页面（`Members.vue`）
// 那一层的事。
import type { TeamsApi } from '@/api/teams'
import type { Team, TeamVisibility } from '@/types'

import { computed } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

const props = defineProps<{
  team: Team
  /** 这一份团队链接；还没读回来时是 `null`。 */
  link: TeamsApi.TeamJoinLink | null
  /** 有事在做：这一段的按钮和开关都按不动。 */
  busy: boolean
  /** 上一次没做成的理由，画成一条红字。 */
  error: string
  /** 刚复制过：按钮上的字换成「已复制」。 */
  copied: boolean
}>()

const emit = defineEmits<{
  /** 换一份新的团队链接，旧的立刻失效。 */
  reset: []
  /** 加入要不要审批。 */
  setApproval: [approval: boolean]
  /** 这个团队在搜索里露不露面。 */
  setVisibility: [visibility: TeamVisibility]
  /** 复制这一条链接。 */
  copy: [url: string]
}>()

const url = computed(() => (props.link ? `${window.location.origin}/team-invites/${props.link.token}` : ''))
</script>

<template>
  <section class="settings-card">
    <h2 class="settings-card__title join__title">{{ t('teams.settings.joinTitle') }}</h2>
    <p v-if="error" role="alert" class="t-body c-danger join__error">{{ error }}</p>

    <div class="srow join__row">
      <span class="srow__k">{{ t('work.teamLink.title') }}</span>
      <div class="join__stack">
        <v-progress-linear v-if="busy && !link" indeterminate :aria-label="t('work.teamLink.loading')" />
        <div v-if="link" class="join__link">
          <v-text-field
            autocomplete="off"
            :model-value="url"
            :aria-label="t('work.teamLink.title')"
            readonly
            hide-details
            density="compact"
            variant="outlined"
            class="join__field"
          />
          <BaseButton kind="secondary" :disabled="busy" @click="emit('copy', url)">
            {{ copied ? t('work.teamLink.copied') : t('work.teamLink.copy') }}
          </BaseButton>
          <BaseButton :disabled="busy" @click="emit('reset')">{{ t('teams.settings.resetLink') }}</BaseButton>
        </div>
        <span class="join__note">{{ t('teams.settings.linkHint') }}</span>
      </div>
    </div>

    <div v-if="link" class="srow join__row">
      <span class="srow__k">{{ t('work.teamLink.approval') }}</span>
      <div class="join__switch">
        <span class="join__note">{{ t('teams.settings.approvalHint') }}</span>
        <v-switch
          :model-value="link.approval"
          :aria-label="t('work.teamLink.approval')"
          :disabled="busy"
          color="primary"
          hide-details
          inset
          density="compact"
          @update:model-value="(value) => emit('setApproval', !!value)"
        />
      </div>
    </div>

    <div class="srow join__row">
      <span class="srow__k">{{ t('teams.settings.visibility') }}</span>
      <v-radio-group
        :model-value="team.visibility"
        :disabled="busy"
        :aria-label="t('teams.settings.visibility')"
        hide-details
        @update:model-value="(value) => emit('setVisibility', value as TeamVisibility)"
      >
        <v-radio value="public">
          <template #label>
            <span class="join__choice">
              <span>{{ t('work.teamLink.public') }}</span>
              <span class="join__note">{{ t('work.teamLink.publicHint') }}</span>
            </span>
          </template>
        </v-radio>
        <v-radio value="stealth">
          <template #label>
            <span class="join__choice">
              <span>{{ t('work.teamLink.stealth') }}</span>
              <span class="join__note">{{ t('teams.settings.stealthHint') }}</span>
            </span>
          </template>
        </v-radio>
      </v-radio-group>
    </div>
  </section>
</template>

<style scoped src="@/styles/settings-card.css"></style>

<style scoped>
.join__title {
  margin: 0;
}

.join__error {
  padding: 0 24px 8px;
  margin: 0;
}

/* 一行放一组控件：标签对着控件的第一行。 */
.join__row {
  grid-template-columns: 120px minmax(0, 1fr);
  gap: 24px;
  align-items: start;
  padding: 16px 24px;
}

.join__row > .srow__k {
  padding-top: 8px;
}

.join__stack {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}

.join__link {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}

.join__field {
  flex: 1 1 260px;
  min-width: 0;
}

.join__switch {
  display: flex;
  gap: 16px;
  align-items: center;
  justify-content: space-between;
  min-width: 0;
}

.join__switch :deep(.v-switch) {
  flex: none;
}

.join__choice {
  display: flex;
  flex-direction: column;
  padding: 4px 0;
  color: var(--ink);
}

.join__note {
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}

@media (max-width: 767.98px) {
  .join__row {
    grid-template-columns: minmax(0, 1fr);
    gap: 8px;
    padding: 16px;
  }

  .join__row > .srow__k {
    padding-top: 0;
  }
}
</style>
