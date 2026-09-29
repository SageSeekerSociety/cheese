<template>
  <!-- 为什么领不了：个人题说是哪一条没满足，团队题逐个团队说。每一条带一个能去改的地方。 -->
  <v-alert
    v-if="task.submitterType === 'USER' && !canJoin && !task.joined && userReasons.length > 0 && !deadlinePassed"
    type="warning"
    rounded="lg"
    title="暂时无法参与"
  >
    <template #text>
      <div class="mt-2">
        <div class="font-weight-medium">{{ userReasons[0]?.message || '你当前无法参与这道题' }}</div>
        <div v-if="userReasons[0]?.code === 'USER_RANK_NOT_HIGH_ENOUGH'" class="mt-2 text-medium-emphasis">
          完成更多基础题目来提升等级，解锁更高难度的题目。
        </div>
        <div v-if="userReasons[0]?.code === 'USER_MISSING_REAL_NAME'" class="mt-2 text-medium-emphasis">
          <div class="d-flex align-center ga-2">
            <span>这道题需要实名信息才能参与。</span>
            <v-btn variant="tonal" size="small" :to="{ name: 'UserSettingsRealName' }">
              前往填写
              <v-icon end>mdi-arrow-right</v-icon>
            </v-btn>
          </div>
        </div>
        <div v-if="userReasons[0]?.code === 'PARTICIPANT_LIMIT_REACHED'" class="mt-2 text-medium-emphasis">
          这道题的名额已满。
        </div>
      </div>
    </template>
  </v-alert>

  <v-alert
    v-if="task.submitterType === 'TEAM' && !canJoin && !task.joined && !deadlinePassed"
    type="warning"
    rounded="lg"
    title="团队不满足参与条件"
  >
    <template #text>
      <div class="mt-2">
        <div v-if="teams.length === 0" class="font-weight-medium">
          你需要创建或加入一个团队才能参与这道题
          <div class="mt-2">
            <v-btn variant="tonal" size="small" :to="{ name: 'HomeTeamsMine' }">
              管理我的团队
              <v-icon end>mdi-arrow-right</v-icon>
            </v-btn>
          </div>
        </div>

        <div v-else-if="!teams.some((team) => team.eligibility.eligible)" class="font-weight-medium">
          你有 {{ teams.length }} 个团队，但没有一个符合这道题的条件
          <v-expansion-panels variant="accordion" class="mt-3">
            <v-expansion-panel v-for="entry in teams" :key="entry.team.id">
              <v-expansion-panel-title class="py-2">
                <div class="d-flex align-center">
                  <v-avatar size="24" class="mr-2">
                    <v-img v-if="entry.team.avatarId" :src="getAvatarUrl(entry.team.avatarId)" alt="" />
                    <v-icon v-else>mdi-account-group</v-icon>
                  </v-avatar>
                  <span>{{ entry.team.name }}</span>
                </div>
              </v-expansion-panel-title>
              <v-expansion-panel-text>
                <div v-for="(reason, index) in entry.eligibility.reasons" :key="index" class="mb-2">
                  <div class="font-weight-medium">
                    <v-icon color="warning" size="small" class="mr-1">mdi-alert-circle</v-icon>
                    {{ reason.message }}
                  </div>
                  <div v-if="reason.code === 'TEAM_SIZE_MIN_NOT_MET'" class="mt-1 text-medium-emphasis">
                    这道题要求团队至少 {{ task.minTeamSize }} 人，邀请更多成员加入团队。
                  </div>
                  <div v-if="reason.code === 'TEAM_SIZE_MAX_EXCEEDED'" class="mt-1 text-medium-emphasis">
                    这道题要求团队最多 {{ task.maxTeamSize }} 人，你的团队人数超出限制。
                  </div>
                  <div v-if="reason.code === 'TEAM_MEMBER_MISSING_REAL_NAME'" class="mt-1">
                    <p class="text-medium-emphasis mb-2">团队中有成员尚未填写实名信息：</p>
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
                  <div v-if="reason.code === 'TEAM_MEMBER_RANK_NOT_HIGH_ENOUGH'" class="mt-1 text-medium-emphasis">
                    团队中有成员等级不足，无法参与这个难度的题目。
                  </div>
                </div>
                <v-btn
                  variant="tonal"
                  size="small"
                  class="mt-2"
                  :to="{ name: 'TeamsDetailMembers', params: { handle: entry.team.handle } }"
                >
                  管理这个团队
                  <v-icon end>mdi-arrow-right</v-icon>
                </v-btn>
              </v-expansion-panel-text>
            </v-expansion-panel>
          </v-expansion-panels>
        </div>

        <div v-else-if="userReasons.length > 0" class="font-weight-medium">
          {{ userReasons[0]?.message || '你当前无法参与这道题' }}
          <div v-if="userReasons[0]?.code === 'USER_RANK_NOT_HIGH_ENOUGH'" class="mt-2 text-medium-emphasis">
            完成更多基础题目来提升等级，解锁更高难度的题目。
          </div>
          <div v-if="userReasons[0]?.code === 'PARTICIPANT_LIMIT_REACHED'" class="mt-2 text-medium-emphasis">
            这道题的名额已满。
          </div>
        </div>
      </div>
    </template>
  </v-alert>
</template>

<script setup lang="ts">
import type { Task } from '@/types'

import { computed } from 'vue'

import { getAvatarUrl } from '@/utils/materials'

const props = defineProps<{ task: Task }>()

const deadlinePassed = computed(() => props.task.deadline != null && props.task.deadline < Date.now())

const canJoin = computed(() =>
  props.task.submitterType === 'USER'
    ? !!props.task.participationEligibility?.user?.eligible
    : !!props.task.participationEligibility?.teams?.some((team) => team.eligibility.eligible)
)

const userReasons = computed(() => props.task.participationEligibility?.user?.reasons ?? [])
const teams = computed(() => props.task.participationEligibility?.teams ?? [])
</script>
