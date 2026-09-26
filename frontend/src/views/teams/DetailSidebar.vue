<template>
  <SecondaryNavigation>
    <!-- 小队信息头部 -->
    <div class="team-header pa-4">
      <div class="d-flex align-center">
        <v-avatar size="48" color="primary" class="team-avatar">
          <v-img :src="getAvatarUrl(teamData?.avatarId)" />
        </v-avatar>
        <div class="ml-3">
          <div class="d-flex align-center">
            <div class="text-h6 team-name">{{ teamData?.name }}</div>
            <v-btn
              v-if="isSelfAdmin"
              class="edit-profile ml-1"
              icon
              size="x-small"
              variant="text"
              :aria-label="t('work.teamProfile.edit')"
              @click="editProfileDialog = true"
            >
              <v-icon size="16">mdi-pencil-outline</v-icon>
            </v-btn>
          </div>
          <div class="text-caption text-medium-emphasis">{{ teamIntro }}</div>
        </div>
      </div>
    </div>

    <v-divider></v-divider>

    <!-- 小队功能区（项目是默认页；频道已随"都归项目"退役） -->
    <div class="px-2 pt-2">
      <v-list density="compact" nav>
        <v-list-item
          :to="{ name: 'TeamsDetailDefault', params: route.params }"
          exact
          prepend-icon="mdi-rocket-launch-outline"
          rounded="lg"
          class="function-item"
        >
          <v-list-item-title>项目</v-list-item-title>
        </v-list-item>

        <v-list-item
          :to="{ name: 'TeamsDetailMembers', params: route.params }"
          prepend-icon="mdi-account-group"
          rounded="lg"
          class="function-item"
        >
          <v-list-item-title>成员管理</v-list-item-title>
          <template #append>
            <v-chip size="x-small" color="primary" variant="tonal" class="ml-2">
              {{ teamMembersCount }}
            </v-chip>
          </template>
        </v-list-item>

        <v-list-item
          :to="{ name: 'TeamsDetailKnowledge', params: route.params }"
          prepend-icon="mdi-book-open-page-variant"
          rounded="lg"
          class="function-item"
        >
          <v-list-item-title>知识库</v-list-item-title>
        </v-list-item>

        <v-list-item
          :to="{ name: 'TeamsDetailCompute', params: route.params }"
          prepend-icon="mdi-server-network"
          rounded="lg"
          class="function-item"
        >
          <v-list-item-title>算力</v-list-item-title>
        </v-list-item>
      </v-list>
    </div>

    <!-- 管理员信息 -->
    <v-divider class="my-2"></v-divider>
    <div class="team-admins px-4 pt-2 pb-4">
      <p class="text-caption text-medium-emphasis mb-2">管理员</p>
      <div class="d-flex align-center">
        <div class="admin-avatars">
          <v-avatar
            v-for="admin in ownerAndAdminExamples"
            :key="admin.id"
            size="28"
            color="surface-variant"
            class="admin-avatar"
          >
            <v-img :src="getAvatarUrl(admin.avatarId)" />
          </v-avatar>
        </div>
        <div class="text-caption text-medium-emphasis ml-2">{{ ownerAndAdminsText }}</div>
      </div>
    </div>
  </SecondaryNavigation>

  <TeamProfileEditDialog
    v-if="teamData"
    v-model="editProfileDialog"
    :team="teamData"
    @updated="emit('updated', $event)"
  />
</template>

<script setup lang="ts">
import type { Team } from '@/types'

import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'

import { getAvatarUrl } from '@/utils/materials'

import TeamProfileEditDialog from './TeamProfileEditDialog.vue'

import SecondaryNavigation from '@/components/common/Navigation/SecondaryNavigation.vue'
import { t } from '@/i18n'

interface Props {
  teamData?: Team
  teamMembersCount: number
}

const props = defineProps<Props>()
const emit = defineEmits<{ updated: [team: Team] }>()

const route = useRoute()

const editProfileDialog = ref(false)

// 看服务端给的 role，不看 admins.examples（那份名单最多 3 个人）；个人小队的本人
// 创建时就是 OWNER，所以这里对个人小队成立，和成员页的 isSelfAdmin 同一条判据。
const isSelfAdmin = computed(() => props.teamData?.role === 'OWNER' || props.teamData?.role === 'ADMIN')

// 个人小队以前把这一行写死成「你的个人团队」，于是它的介绍改了也没处看。改成
// 「有介绍就显示介绍，没有才回落成那句话」—— 团队小队本来就是这么显示的。
const teamIntro = computed(() => props.teamData?.intro || (props.teamData?.personal ? '你的个人团队' : ''))

const ownerAndAdminExamples = computed(() => {
  if (!props.teamData) {
    return []
  }
  return [props.teamData.owner, ...(props.teamData.admins.examples || [])]
})

const ownerAndAdminTotal = computed(() => {
  if (!props.teamData) {
    return 0
  }
  return 1 + (props.teamData.admins.total || 0)
})

const ownerAndAdminsText = computed(() => {
  if (!ownerAndAdminTotal.value) {
    return '暂无管理员'
  } else if (ownerAndAdminTotal.value === 1) {
    return `创建者 ${ownerAndAdminExamples.value[0]!.nickname}`
  } else {
    return `创建者 ${ownerAndAdminExamples.value[0]!.nickname} 和 ${ownerAndAdminTotal.value - 1} 位管理员`
  }
})
</script>

<style scoped lang="scss">
.team-header {
  transition: all 0.3s ease;
}

.team-avatar {
  box-shadow: var(--shadow-1);
  /* 头像外面那圈是把它从底上"抠"出来，所以等于它背后的面色 */
  border: 2px solid var(--surface);
}

.team-name {
  font-weight: 500;
  line-height: 1.2;
}

/* 铅笔是「看见了才想起来能改」的东西：平时压暗，鼠标上来或键盘聚焦才亮起来 */
.edit-profile {
  opacity: 0.55;
  transition: opacity 0.2s ease;

  &:hover,
  &:focus-visible {
    opacity: 1;
  }
}

.team-header:hover .edit-profile {
  opacity: 1;
}

.function-item {
  height: 36px;
  margin-bottom: 2px;
  transition: all 0.2s ease;

  &:hover {
    background-color: var(--fill);
  }
}

.admin-avatars {
  display: flex;
  flex-wrap: nowrap;
  overflow: hidden;

  .admin-avatar {
    border: 2px solid var(--surface);
    transition: all 0.3s ease;
  }

  .admin-avatar:not(:first-child) {
    margin-left: -8px;
  }

  &:hover {
    overflow: visible;

    .admin-avatar:not(:first-child) {
      margin-left: 4px;
    }
  }

  .admin-avatar:nth-child(1) {
    z-index: 10;
  }
  .admin-avatar:nth-child(2) {
    z-index: 9;
  }
  .admin-avatar:nth-child(3) {
    z-index: 8;
  }
}
</style>
