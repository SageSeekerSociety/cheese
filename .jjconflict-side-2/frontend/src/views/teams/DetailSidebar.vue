<template>
  <SecondaryNavigation>
    <!-- 小队信息头部 -->
    <div class="team-header pa-4">
      <div class="d-flex align-center">
        <v-avatar size="48" color="primary" class="team-avatar">
          <v-img :src="getAvatarUrl(teamData?.avatarId)" />
        </v-avatar>
        <div class="ml-3">
          <div class="text-h6 team-name">{{ teamData?.name }}</div>
          <div class="text-caption text-medium-emphasis">
            {{ teamData?.personal ? '你的个人小队' : teamData?.intro }}
          </div>
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
            color="grey-lighten-2"
            class="admin-avatar"
          >
            <v-img :src="getAvatarUrl(admin.avatarId)" />
          </v-avatar>
        </div>
        <div class="text-caption text-medium-emphasis ml-2">{{ ownerAndAdminsText }}</div>
      </div>
    </div>
  </SecondaryNavigation>
</template>

<script setup lang="ts">
import type { Team } from '@/types'

import { computed } from 'vue'
import { useRoute } from 'vue-router'

import { getAvatarUrl } from '@/utils/materials'

import SecondaryNavigation from '@/components/common/Navigation/SecondaryNavigation.vue'

interface Props {
  teamData?: Team
  teamMembersCount: number
}

const props = defineProps<Props>()

const route = useRoute()

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
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.08);
  border: 2px solid white;
}

.team-name {
  font-weight: 500;
  line-height: 1.2;
}

.function-item {
  height: 36px;
  margin-bottom: 2px;
  transition: all 0.2s ease;

  &:hover {
    background-color: rgba(0, 0, 0, 0.04);
  }
}

.admin-avatars {
  display: flex;
  flex-wrap: nowrap;
  overflow: hidden;

  .admin-avatar {
    border: 2px solid #fff;
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
