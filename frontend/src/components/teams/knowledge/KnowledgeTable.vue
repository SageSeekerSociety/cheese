<script setup lang="ts">
// 知识库的列表视图：一行一条。
//
// 和 `KnowledgeGrid` 是同一份数据的另一种画法，区别只在这两处：这里多一栏
// 「操作」（看 / 打开 / 删），标签折到两个；卡上那颗删除键没有，这里按
// `canEditKnowledge` 给 —— 也就是「是不是自己放上去的」。
import type { Knowledge } from '@/types'

import { getAvatarUrl } from '@/utils/materials'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'
import { canEditKnowledge, formatDay, resourceTypeIcon, resourceTypeName } from '@/lib/knowledgeFormat'

defineOptions({ name: 'KnowledgeTable' })

defineProps<{ items: Knowledge[]; ownerId?: number }>()

const emit = defineEmits<{
  open: [resource: Knowledge]
  openLink: [resource: Knowledge]
  delete: [resource: Knowledge]
}>()
</script>

<template>
  <v-table class="resource-table rounded-lg">
    <thead>
      <tr>
        <th>{{ t('teams.knowledge.name') }}</th>
        <th>{{ t('teams.knowledge.type') }}</th>
        <th>{{ t('teams.knowledge.creator') }}</th>
        <th>{{ t('teams.knowledge.createdAt') }}</th>
        <th>{{ t('teams.knowledge.tags') }}</th>
        <th>{{ t('teams.knowledge.actions') }}</th>
      </tr>
    </thead>
    <tbody>
      <tr v-for="resource in items" :key="resource.id" class="resource-row" @click="emit('open', resource)">
        <td>
          <div class="d-flex align-center">
            <v-icon
              :icon="resourceTypeIcon(resource.type, resource.material?.type)"
              size="20"
              class="mr-3 resource-type-icon"
            ></v-icon>
            <div>
              <div class="font-weight-medium resource-title">{{ resource.name }}</div>
              <div v-if="resource.description" class="text-caption text-medium-emphasis resource-description-list">
                {{ resource.description }}
              </div>
            </div>
          </div>
        </td>
        <td>{{ resourceTypeName(resource.type, resource.material?.type) }}</td>
        <td>
          <div class="d-flex align-center">
            <v-avatar size="24" rounded="circle" color="surface-variant" class="mr-2">
              <v-img :src="getAvatarUrl(resource.creator.avatarId)"></v-img>
            </v-avatar>
            <span>{{ resource.creator.nickname }}</span>
          </div>
        </td>
        <td>{{ formatDay(resource.createdAt) }}</td>
        <td>
          <div class="resource-tags">
            <v-chip
              v-for="tag in resource.labels ? resource.labels.slice(0, 2) : []"
              :key="tag"
              size="x-small"
              variant="tonal"
              class="mr-1"
            >
              {{ tag }}
            </v-chip>
            <v-chip v-if="resource.labels && resource.labels.length > 2" size="x-small" variant="tonal">
              +{{ resource.labels.length - 2 }}
            </v-chip>
          </div>
        </td>
        <td>
          <!-- ga-4: three icon buttons side by side. On a coarse pointer each
               one widens its hit area to 44x44, so they need 16px between them
               or they cover each other (see the ::before in BaseButton). -->
          <div class="d-flex ga-4">
            <BaseButton
              kind="ghost"
              size="sm"
              density="comfortable"
              icon="mdi-eye"
              :aria-label="t('teams.knowledge.openResource')"
              @click.stop="emit('open', resource)"
            />
            <BaseButton
              kind="ghost"
              size="sm"
              density="comfortable"
              icon="mdi-open-in-new"
              :aria-label="t('navigation.palette.newTab')"
              @click.stop="emit('openLink', resource)"
            />
            <BaseButton
              v-if="canEditKnowledge(resource, ownerId)"
              kind="ghost"
              size="sm"
              density="comfortable"
              icon="mdi-delete"
              :aria-label="t('teams.knowledge.deleteResource')"
              @click.stop="emit('delete', resource)"
            />
          </div>
        </td>
      </tr>
    </tbody>
  </v-table>
</template>

<style scoped lang="scss">
.resource-table {
  border: 1px solid var(--line);
}

.resource-row {
  cursor: pointer;
  transition: background-color 0.2s ease;

  &:hover {
    background-color: var(--fill);
  }
}

.resource-type-icon {
  flex-shrink: 0;
}

.resource-title {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  line-height: 1.4;
}

.resource-description-list {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 300px;
}

// 响应式调整
@media (max-width: 600px) {
  .resource-description-list {
    max-width: 150px;
  }
}
</style>
