<template>
  <!-- 为什么领不了：个人题说是哪一条没满足，团队题逐个团队说。每一条带一个能去改的地方。 -->
  <v-alert
    v-if="task.submitterType === 'USER' && !canJoin && !task.joined && userReasons.length > 0 && !deadlinePassed"
    type="warning"
    rounded="lg"
    :title="t('tasks.eligibilityAlert.cannotJoinTitle')"
  >
    <template #text>
      <div class="mt-2">
        <div class="font-weight-medium">{{ reasonText(userReasons[0]?.code) }}</div>
        <div v-if="userReasons[0]?.code === 'USER_RANK_NOT_HIGH_ENOUGH'" class="mt-2 text-medium-emphasis">
          {{ t('tasks.eligibilityAlert.rankHint') }}
        </div>
        <div v-if="userReasons[0]?.code === 'MISSING_REAL_NAME'" class="mt-2 text-medium-emphasis">
          <div class="d-flex align-center ga-2">
            <span>{{ t('tasks.eligibilityAlert.needRealName') }}</span>
            <BaseButton kind="secondary" size="sm" :to="{ name: 'UserSettingsRealName' }" append-icon="mdi-arrow-right">
              {{ t('tasks.eligibilityAlert.goFillIn') }}
            </BaseButton>
          </div>
        </div>
      </div>
    </template>
  </v-alert>

  <v-alert
    v-if="task.submitterType === 'TEAM' && !canJoin && !task.joined && !deadlinePassed"
    type="warning"
    rounded="lg"
    :title="t('tasks.eligibilityAlert.teamNotEligibleTitle')"
  >
    <template #text>
      <div class="mt-2">
        <div v-if="teams.length === 0" class="font-weight-medium">
          {{ t('tasks.eligibilityAlert.needTeam') }}
          <div class="mt-2">
            <BaseButton kind="secondary" size="sm" :to="{ name: 'HomeTeamsMine' }" append-icon="mdi-arrow-right">
              {{ t('tasks.eligibilityAlert.manageMyTeams') }}
            </BaseButton>
          </div>
        </div>

        <div v-else-if="!teams.some((team) => team.eligibility.eligible)" class="font-weight-medium">
          {{ t('tasks.eligibilityAlert.teamsNoneEligible', teams.length) }}
          <v-expansion-panels variant="accordion" class="mt-3">
            <v-expansion-panel v-for="entry in teams" :key="entry.team.id">
              <v-expansion-panel-title class="py-2">
                <div class="d-flex align-center">
                  <UserAvatar
                    kind="org"
                    :avatar="getAvatarUrl(entry.team.avatarId)"
                    :name="entry.team.name"
                    size="24"
                    class="mr-2"
                  />
                  <span>{{ entry.team.name }}</span>
                </div>
              </v-expansion-panel-title>
              <v-expansion-panel-text>
                <div v-for="(reason, index) in entry.eligibility.reasons" :key="index" class="mb-2">
                  <div class="font-weight-medium">
                    <v-icon color="warning" size="small" class="mr-1">mdi-alert-circle</v-icon>
                    {{ reasonText(reason.code) }}
                  </div>
                  <div v-if="reason.code === 'TEAM_TOO_SMALL'" class="mt-1 text-medium-emphasis">
                    {{ t('tasks.eligibilityAlert.teamTooSmall', { count: task.minTeamSize }) }}
                  </div>
                  <div v-if="reason.code === 'TEAM_TOO_LARGE'" class="mt-1 text-medium-emphasis">
                    {{ t('tasks.eligibilityAlert.teamTooBig', { count: task.maxTeamSize }) }}
                  </div>
                  <div v-if="reason.code === 'TEAM_MEMBER_MISSING_REAL_NAME'" class="mt-1">
                    <p class="text-medium-emphasis mb-2">{{ t('tasks.eligibilityAlert.memberMissingRealName') }}</p>
                    <v-list
                      v-if="entry.team.memberRealNameStatus"
                      density="compact"
                      class="bg-surface-light rounded-lg pa-0 mb-2"
                    >
                      <v-list-item
                        v-for="member in entry.team.memberRealNameStatus.filter((m) => !m.hasRealNameInfo)"
                        :key="member.memberId"
                        density="compact"
                      >
                        <template #prepend>
                          <v-icon size="small" color="error" class="mr-2">mdi-account-alert</v-icon>
                        </template>
                        <v-list-item-title class="text-body-2">{{ member.userName }}</v-list-item-title>
                      </v-list-item>
                    </v-list>
                  </div>
                </div>
                <BaseButton
                  kind="secondary"
                  size="sm"
                  class="mt-2"
                  :to="{ name: 'TeamsDetailMembers', params: { handle: entry.team.handle } }"
                  append-icon="mdi-arrow-right"
                >
                  {{ t('tasks.eligibilityAlert.manageThisTeam') }}
                </BaseButton>
              </v-expansion-panel-text>
            </v-expansion-panel>
          </v-expansion-panels>
        </div>

        <div v-else-if="userReasons.length > 0" class="font-weight-medium">
          {{ reasonText(userReasons[0]?.code) }}
          <div v-if="userReasons[0]?.code === 'USER_RANK_NOT_HIGH_ENOUGH'" class="mt-2 text-medium-emphasis">
            {{ t('tasks.eligibilityAlert.rankHint') }}
          </div>
        </div>
      </div>
    </template>
  </v-alert>
</template>

<script setup lang="ts">
import type { Task } from '@/types'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import { getAvatarUrl } from '@/utils/materials'

import BaseButton from '@/components/base/BaseButton.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import { eligibilityReasonKey } from '@/views/tasks/eligibilityReason'

const props = defineProps<{ task: Task }>()
const { t } = useI18n()

function reasonText(code: string | undefined): string {
  return t(eligibilityReasonKey(code))
}

const deadlinePassed = computed(() => props.task.deadline != null && props.task.deadline < Date.now())

const canJoin = computed(() =>
  props.task.submitterType === 'USER'
    ? !!props.task.participationEligibility?.user?.eligible
    : !!props.task.participationEligibility?.teams?.some((team) => team.eligibility.eligible)
)

const userReasons = computed(() => props.task.participationEligibility?.user?.reasons ?? [])
const teams = computed(() => props.task.participationEligibility?.teams ?? [])
</script>
