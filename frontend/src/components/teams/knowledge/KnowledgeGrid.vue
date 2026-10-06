<script setup lang="ts">
// 知识库的网格视图：一条一张卡。
//
// 只管画：拿 `items`，报「点开了这一条」和「点了在新窗口打开」。判断这一条是
// 什么、叫什么、几号放的、谁放的，全在 `lib/knowledgeFormat.ts`；那一层不 import
// api / 路由，所以这里也用得。
//
// 预览格的固定高度和四支分类底色都在这一件的样式块里：这一件在预览站里要
// 单独画出来，它需要什么就带着什么。色值本身是 `src/style.css` 的
// `--category-*`，不是写在这里的字面量。
import type { Knowledge } from '@/types'

import { getAvatarUrl } from '@/utils/materials'

import BaseButton from '@/components/base/BaseButton.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import { t } from '@/i18n'
import { formatDay, resourceTypeIcon, resourceTypeName } from '@/lib/knowledgeFormat'

defineOptions({ name: 'KnowledgeGrid' })

defineProps<{ items: Knowledge[] }>()

const emit = defineEmits<{
  open: [resource: Knowledge]
  openLink: [resource: Knowledge]
}>()
</script>

<template>
  <v-row>
    <v-col v-for="resource in items" :key="resource.id" cols="12" sm="6" md="4" lg="3">
      <v-hover v-slot="{ isHovering, props }">
        <v-card
          v-bind="props"
          class="resource-card fill-height"
          rounded="lg"
          :class="isHovering ? 'card-hover' : ''"
          @click="emit('open', resource)"
        >
          <!-- 资料预览区 -->
          <div class="resource-preview" :class="`resource-type-${resource.type.toLowerCase()}`">
            <v-icon
              v-if="!resource.thumbnail"
              :icon="resourceTypeIcon(resource.type, resource.material?.type)"
              size="48"
              class="resource-icon"
            ></v-icon>
            <v-img v-else :src="resource.thumbnail" class="resource-thumbnail" cover></v-img>
          </div>

          <!-- 资料信息区 -->
          <v-card-text class="pa-4">
            <div class="d-flex align-center mb-1">
              <v-icon
                :icon="resourceTypeIcon(resource.type, resource.material?.type)"
                size="16"
                class="mr-2 resource-type-icon"
              ></v-icon>
              <span class="text-caption text-medium-emphasis">{{
                resourceTypeName(resource.type, resource.material?.type)
              }}</span>
              <v-spacer></v-spacer>
              <span class="text-caption text-medium-emphasis">{{ formatDay(resource.createdAt) }}</span>
            </div>

            <h3 class="text-subtitle-1 font-weight-medium resource-title mb-1" data-user-content>
              {{ resource.name }}
            </h3>
            <p v-if="resource.description" class="text-body-2 resource-description" data-user-content>
              {{ resource.description }}
            </p>

            <!-- 标签 -->
            <div v-if="resource.labels && resource.labels.length > 0" class="resource-tags mt-2" data-user-content>
              <v-chip
                v-for="tag in resource.labels.slice(0, 3)"
                :key="tag"
                size="x-small"
                variant="tonal"
                class="mr-1 mb-1"
              >
                {{ tag }}
              </v-chip>
              <v-chip v-if="resource.labels.length > 3" size="x-small" variant="tonal" class="mb-1">
                +{{ resource.labels.length - 3 }}
              </v-chip>
            </div>
          </v-card-text>

          <!-- 底部信息 -->
          <v-card-actions class="pa-4 pt-0">
            <UserAvatar :avatar="getAvatarUrl(resource.creator.avatarId)" :name="resource.creator.nickname" size="24" />
            <span class="text-caption ml-2">{{ resource.creator.nickname }}</span>
            <v-spacer></v-spacer>
            <!-- 删除键在网格里没有：卡上那一颗是「打开」。只有列表视图给了删除。 -->
            <BaseButton
              kind="ghost"
              size="sm"
              density="comfortable"
              icon="mdi-open-in-new"
              :aria-label="t('navigation.palette.newTab')"
              @click.stop="emit('openLink', resource)"
            />
          </v-card-actions>
        </v-card>
      </v-hover>
    </v-col>
  </v-row>
</template>

<style scoped lang="scss">
.resource-card {
  border: 1px solid var(--line);
  overflow: hidden;
  transition: border-color var(--dur-quick) var(--ease-standard);

  &.card-hover {
    border-color: var(--line-2);
  }
}

.resource-icon {
  opacity: 0.6;
}

.resource-thumbnail {
  width: 100%;
  height: 100%;
}

.resource-title {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  line-height: 1.4;
}

.resource-description {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  color: var(--muted);
  line-height: 1.4;
  min-height: 2.8em;
}

/* 资料预览区那一格：固定高度 + 按类型给的分类底色。
   四支分类色是 `src/style.css` 里的 `--category-*`（色相本身就是信息，
   两个主题下同一个色相），不是写在这一件里的字面量 —— 这一件在预览站里要
   单独画出来，它需要什么就该带着什么。 */
.resource-preview {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 140px;
  background-color: var(--fill);

  &.resource-type-material {
    background-color: var(--category-material);
  }

  &.resource-type-text {
    background-color: var(--category-text);
  }

  &.resource-type-link {
    background-color: var(--category-link);
  }

  &.resource-type-code {
    background-color: var(--category-code);
  }
}

.resource-type-icon {
  flex-shrink: 0;
}
</style>
