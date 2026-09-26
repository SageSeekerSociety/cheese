<template>
  <!--
    我的小组（成员）。他在这一门课里和谁一组、组里还有谁，以及别人拉他、他申请别人
    那两件正在等的事 —— 三样都不经过管理员。

    「我这门课的组」问的是服务端（`GET /spaces/{id}/course/my-group`）：它按我的项目
    取到项目的 teamId，所以这一格和管理员那一屏的分组**是同一份事实**，不是前端又猜了
    一遍。其余三块是团队本来就有的接口（my-teams / 邀请 / 申请）。

    建组、接受邀请、撤回申请都是成员自己的权限（团队那侧认的是团队角色），所以这一
    屏真的能动手；管理员那一屏改不了组，原因见 `People.vue`。
  -->
  <v-sheet flat rounded="lg" class="pa-4">
    <h1 class="text-h6 mb-3">{{ t('spaces.course.team.title') }}</h1>

    <div v-if="loading" class="text-center pa-4">
      <v-progress-circular indeterminate color="primary"></v-progress-circular>
    </div>

    <template v-else>
      <v-sheet v-if="group" flat rounded="lg" class="section pa-4 mb-4">
        <div class="d-flex align-center flex-wrap ga-2 mb-3">
          <v-icon icon="mdi-account-multiple-outline" size="18"></v-icon>
          <span class="font-weight-medium">{{ group.name }}</span>
          <v-chip size="x-small" variant="tonal">
            {{ t('spaces.course.people.memberCount', { n: group.members.length }) }}
          </v-chip>
        </div>
        <div class="d-flex flex-wrap ga-3">
          <div v-for="person in group.members" :key="person.id" class="d-flex align-center ga-2">
            <v-avatar size="28" color="surface-variant">
              <v-img :src="getAvatarUrl(person.avatarId ?? undefined)" />
            </v-avatar>
            <span class="text-body-2">{{ displayName(person) }}</span>
          </div>
        </div>
      </v-sheet>

      <v-sheet v-else flat rounded="lg" class="section pa-6 text-center mb-4">
        <p class="text-medium-emphasis mb-2">{{ t('spaces.course.team.noGroupYet') }}</p>
        <p class="text-caption text-medium-emphasis mb-3">
          {{ t('spaces.course.team.howToJoin') }}
        </p>
        <v-btn variant="tonal" color="primary" prepend-icon="mdi-account-plus-outline" @click="createOpen = true">
          {{ t('spaces.course.team.create') }}
        </v-btn>
      </v-sheet>

      <template v-if="invitations.length">
        <div class="text-subtitle-2 mb-2">{{ t('spaces.course.team.invitations') }}</div>
        <v-sheet flat rounded="lg" class="section pa-2 mb-4">
          <v-list density="comfortable" class="pa-0">
            <v-list-item v-for="invitation in invitations" :key="invitation.id">
              <v-list-item-title>{{ invitation.team.name }}</v-list-item-title>
              <v-list-item-subtitle>
                {{ t('spaces.course.team.invitedBy', { name: displayName(invitation.initiator) }) }}
              </v-list-item-subtitle>
              <template #append>
                <div class="d-flex ga-2">
                  <v-btn size="small" variant="tonal" color="primary" @click="accept(invitation.id)">
                    {{ t('spaces.course.team.accept') }}
                  </v-btn>
                  <v-btn size="small" variant="text" @click="decline(invitation.id)">
                    {{ t('spaces.course.team.decline') }}
                  </v-btn>
                </div>
              </template>
            </v-list-item>
          </v-list>
        </v-sheet>
      </template>

      <template v-if="requests.length">
        <div class="text-subtitle-2 mb-2">{{ t('spaces.course.team.requests') }}</div>
        <v-sheet flat rounded="lg" class="section pa-2 mb-4">
          <v-list density="comfortable" class="pa-0">
            <v-list-item v-for="request in requests" :key="request.id">
              <v-list-item-title>{{ request.team.name }}</v-list-item-title>
              <v-list-item-subtitle>{{ t('spaces.course.team.waiting') }}</v-list-item-subtitle>
              <template #append>
                <v-btn size="small" variant="text" @click="cancel(request.id)">
                  {{ t('spaces.course.team.withdraw') }}
                </v-btn>
              </template>
            </v-list-item>
          </v-list>
        </v-sheet>
      </template>

      <div class="text-subtitle-2 mb-2">{{ t('spaces.course.team.myTeams') }}</div>
      <v-sheet v-if="myTeams.length" flat rounded="lg" class="section pa-2">
        <v-list density="comfortable" class="pa-0">
          <v-list-item v-for="team in myTeams" :key="team.id">
            <v-list-item-title>{{ team.name }}</v-list-item-title>
            <v-list-item-subtitle>
              {{ t('spaces.course.people.memberCount', { n: team.members.total }) }}
            </v-list-item-subtitle>
            <template #append>
              <v-chip v-if="group && group.id === team.id" size="small" variant="tonal" color="primary">
                {{ t('spaces.course.team.thisCourseGroup') }}
              </v-chip>
            </template>
          </v-list-item>
        </v-list>
      </v-sheet>

      <v-sheet v-else flat rounded="lg" class="section pa-6 text-center">
        <p class="text-medium-emphasis mb-0">{{ t('spaces.course.team.noTeams') }}</p>
      </v-sheet>
    </template>

    <v-dialog v-model="createOpen" max-width="420">
      <v-card>
        <v-card-title>{{ t('spaces.course.team.create') }}</v-card-title>
        <v-card-text>
          <v-text-field
            v-model="newTeamName"
            :label="t('spaces.course.team.nameLabel')"
            variant="outlined"
            density="comfortable"
            autocomplete="off"
          ></v-text-field>
        </v-card-text>
        <v-card-actions>
          <v-spacer></v-spacer>
          <v-btn variant="text" @click="createOpen = false">{{ t('spaces.course.team.cancel') }}</v-btn>
          <v-btn color="primary" :disabled="!newTeamName.trim()" @click="createTeam">
            {{ t('spaces.course.team.confirmCreate') }}
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </v-sheet>
</template>

<script setup lang="ts">
import type { CourseRosterPerson } from '@/types'
import type { Team, TeamMembershipApplication } from '@/types/teams'

import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'

import { getAvatarUrl } from '@/utils/materials'

import { SpacesApi } from '@/network/api/spaces'
import { TeamsApi } from '@/network/api/teams'

const { t } = useI18n()
const route = useRoute()

const spaceId = String(route.params.spaceId)
const loading = ref(true)
const group = ref<{ id: number; name: string; members: CourseRosterPerson[] } | null>(null)
const myTeams = ref<Team[]>([])
const invitations = ref<TeamMembershipApplication[]>([])
const requests = ref<TeamMembershipApplication[]>([])
const createOpen = ref(false)
const newTeamName = ref('')

function displayName(person: { nickname?: string; username: string }): string {
  return person.nickname || person.username
}

async function load() {
  const id = Number(spaceId)
  const [mine, teams, invited, asked] = await Promise.allSettled([
    Number.isFinite(id) && id > 0 ? SpacesApi.getMyCourseGroup(id) : Promise.reject(),
    TeamsApi.getMyTeams(),
    TeamsApi.listMyInvitations({ status: 'PENDING' }),
    TeamsApi.listMyJoinRequests({ status: 'PENDING' }),
  ])
  // 一块坏了不连坐另外三块：这门课没有组（或者板还没过审）时，其余三块照样能用。
  group.value = mine.status === 'fulfilled' ? mine.value.data.team : null
  myTeams.value = teams.status === 'fulfilled' ? teams.value.data.teams : []
  invitations.value = invited.status === 'fulfilled' ? invited.value.data.invitations : []
  requests.value = asked.status === 'fulfilled' ? asked.value.data.requests : []
}

async function refresh() {
  await load()
}

async function accept(invitationId: number) {
  await TeamsApi.acceptInvitation(invitationId)
  await refresh()
}

async function decline(invitationId: number) {
  await TeamsApi.declineInvitation(invitationId)
  await refresh()
}

async function cancel(requestId: number) {
  await TeamsApi.cancelMyJoinRequest(requestId)
  await refresh()
}

async function createTeam() {
  await TeamsApi.create({
    name: newTeamName.value.trim(),
    intro: '',
    description: '',
    avatarId: 1,
  })
  newTeamName.value = ''
  createOpen.value = false
  await refresh()
}

onMounted(async () => {
  await load()
  loading.value = false
})
</script>

<style scoped lang="scss">
.section {
  border: 1px solid var(--line);
}
</style>
