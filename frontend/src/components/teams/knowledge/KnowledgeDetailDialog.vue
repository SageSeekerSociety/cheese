<script setup lang="ts">
// 资料详情：预览（图 / 视频 / 音频 / 文档 / 富文本 / 链接 / 代码）+ 资料信息 + 原始讨论。
//
// 三块都不判断「这一条归谁」：能不能删由页算好 `ownerId` 递进来，删除本身报上去
// （`delete`），由页去问那一句再打接口 —— 组件这一层不弹确认框、不发请求。
//
// **`.code-block` 的底色不在这里**：那是刻意反色的两块颜色，钉在
// `views/teams/detail/Knowledge.vue` 的样式块里，理由写在那边。
import type { Knowledge, KnowledgeContentData } from '@/types'
import type { AudioMeta, FileMeta, ImageMeta, VideoMeta } from '@/types/materials'

import { computed } from 'vue'

import { getAvatarUrl } from '@/utils/materials'

import TipTapEditor from '@/components/common/Editor/TipTapEditor.vue'
import {
  canEditKnowledge,
  formatDetailDate,
  formatDuration,
  formatFileSize,
  formatMessageTime,
  resourceTypeIcon,
  resourceTypeName,
} from '@/lib/knowledgeFormat'

defineOptions({ name: 'KnowledgeDetailDialog' })

const props = defineProps<{
  modelValue: boolean
  resource: Knowledge | null
  content: KnowledgeContentData
  ownerId?: number
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  'update:content': [value: KnowledgeContentData]
  openLink: [resource: Knowledge]
  delete: [resource: Knowledge]
}>()

/** 富文本预览那一格：编辑器在只读之外还能被敲两下，所以要把改动交回去。 */
const richText = computed({
  get: () => props.content.richText,
  set: (value) => emit('update:content', { ...props.content, richText: value }),
})
</script>

<template>
  <v-dialog :model-value="modelValue" max-width="700" @update:model-value="emit('update:modelValue', $event)">
    <v-card v-if="resource" class="resource-detail-card">
      <v-card-title class="d-flex justify-space-between align-center pa-4">
        <div>{{ resource.name }}</div>
        <v-btn icon="mdi-close" variant="text" @click="emit('update:modelValue', false)"></v-btn>
      </v-card-title>
      <v-divider></v-divider>

      <v-card-text class="pa-4">
        <!-- 资料预览 -->
        <div class="resource-full-preview mb-4">
          <!-- 图片预览 -->
          <v-img
            v-if="resource.type === 'MATERIAL' && resource.material?.type === 'image'"
            :src="resource.material.url"
            :width="(resource.material.meta as ImageMeta).width"
            :height="(resource.material.meta as ImageMeta).height"
            max-height="400"
            class="mx-auto resource-image"
            contain
          ></v-img>

          <!-- 视频预览 -->
          <video
            v-else-if="resource.type === 'MATERIAL' && resource.material?.type === 'video'"
            controls
            class="mx-auto resource-video"
            max-height="400"
            max-width="100%"
          >
            <source :src="resource.material.url" />
            您的浏览器不支持视频播放
          </video>

          <!-- 音频预览 -->
          <audio
            v-else-if="resource.type === 'MATERIAL' && resource.material?.type === 'audio'"
            controls
            class="mx-auto resource-audio d-block my-4"
            style="width: 100%"
          >
            <source :src="resource.material.url" />
            您的浏览器不支持音频播放
          </audio>

          <!-- 文档预览 -->
          <v-sheet
            v-else-if="resource.type === 'MATERIAL' && resource.material?.type === 'file'"
            color="surface-light"
            class="pa-4 document-preview rounded-lg"
          >
            <v-icon
              :icon="resourceTypeIcon(resource.type, resource.material.type)"
              size="48"
              class="mb-2 d-block mx-auto"
            ></v-icon>
            <div class="text-center">
              <div class="text-body-2 mb-2">
                {{ (resource.material.meta as FileMeta).name || '该文档需要在外部程序中查看' }}
              </div>
              <div class="text-caption text-medium-emphasis mb-3">
                {{ formatFileSize((resource.material.meta as FileMeta).size) }}
              </div>
              <v-btn color="primary" size="small" @click="emit('openLink', resource)"> 打开文档 </v-btn>
            </div>
          </v-sheet>

          <!-- 富文本内容预览 -->
          <v-sheet v-else-if="resource.type === 'TEXT' && content.richText" class="pa-4 text-preview rounded-lg">
            <div class="rich-text-preview">
              <!-- 这里应该渲染富文本内容，可以用TipTap的只读模式 -->
              <TipTapEditor v-model="richText" hide-toolbar output="json" :min-height="150" />
            </div>
          </v-sheet>

          <!-- 链接预览 -->
          <v-sheet v-else-if="resource.type === 'LINK'" color="surface-light" class="pa-4 link-preview rounded-lg">
            <div class="d-flex flex-column flex-md-row">
              <v-img
                v-if="resource.thumbnail"
                :src="resource.thumbnail"
                height="80"
                width="120"
                class="rounded-lg mr-4 mb-3 mb-md-0"
                cover
              ></v-img>
              <div>
                <h3 class="text-h6 mb-1">{{ content.title || resource.name }}</h3>
                <p class="text-body-2 mb-2">{{ content.description || resource.description }}</p>
                <div class="text-caption text-medium-emphasis mb-3 text-truncate">{{ content.url }}</div>
                <v-btn color="primary" size="small" @click="emit('openLink', resource)"> 访问链接 </v-btn>
              </div>
            </div>
          </v-sheet>

          <!-- 代码片段预览 -->
          <v-sheet v-else-if="resource.type === 'CODE'" color="surface-light" class="pa-4 code-preview rounded-lg">
            <div class="d-flex align-center mb-2">
              <div class="text-subtitle-1 font-weight-medium">代码片段</div>
              <v-chip v-if="content.language" class="ml-2" size="small" variant="tonal">
                {{ content.language }}
              </v-chip>
            </div>
            <!-- 底色不写在这里：代码块是【故意反色】的元素，两个主题要两套值，
                 见 <style> 里的 .code-block -->
            <v-sheet class="pa-4 rounded-lg code-block">
              <pre class="language-{{ content.language || 'javascript' }}"><code>{{ content.code }}</code></pre>
            </v-sheet>
          </v-sheet>

          <div v-else class="text-center py-4">
            <v-icon :icon="resourceTypeIcon(resource.type, resource.material?.type)" size="64"></v-icon>
            <div class="mt-2 text-body-2">此类型的资料没有预览</div>
          </div>
        </div>

        <!-- 资料信息 -->
        <v-sheet color="surface-light" rounded="lg" class="pa-4 mb-4">
          <div class="text-subtitle-1 font-weight-medium mb-2">资料信息</div>
          <div class="d-flex resource-info-row">
            <div class="resource-info-label">类型</div>
            <div>{{ resourceTypeName(resource.type, resource.material?.type) }}</div>
          </div>
          <div v-if="resource.material?.type === 'file'" class="d-flex resource-info-row">
            <div class="resource-info-label">文件名</div>
            <div>{{ (resource.material.meta as FileMeta).name }}</div>
          </div>
          <div v-if="resource.material" class="d-flex resource-info-row">
            <div class="resource-info-label">大小</div>
            <div>{{ formatFileSize(resource.material.meta.size) }}</div>
          </div>
          <div
            v-if="resource.material?.type === 'video' || resource.material?.type === 'audio'"
            class="d-flex resource-info-row"
          >
            <div class="resource-info-label">时长</div>
            <div>{{ formatDuration((resource.material.meta as VideoMeta | AudioMeta).duration) }}</div>
          </div>
          <div class="d-flex resource-info-row">
            <div class="resource-info-label">添加者</div>
            <div class="d-flex align-center">
              <v-avatar size="24" color="surface-variant" class="mr-2">
                <v-img :src="getAvatarUrl(resource.creator.avatarId)"></v-img>
              </v-avatar>
              <span>{{ resource.creator.nickname }}</span>
            </div>
          </div>
          <div class="d-flex resource-info-row">
            <div class="resource-info-label">添加时间</div>
            <div>{{ formatDetailDate(resource.createdAt) }}</div>
          </div>
          <div class="d-flex resource-info-row">
            <div class="resource-info-label">来源频道</div>
            <div>{{ resource.sourceChannel?.name || '未知频道' }}</div>
          </div>
          <div v-if="resource.description" class="d-flex resource-info-row">
            <div class="resource-info-label">描述</div>
            <div>{{ resource.description }}</div>
          </div>
          <div v-if="resource.labels && resource.labels.length > 0" class="d-flex resource-info-row">
            <div class="resource-info-label">标签</div>
            <div class="d-flex flex-wrap">
              <v-chip v-for="tag in resource.labels" :key="tag" size="small" variant="tonal" class="mr-1 mb-1">
                {{ tag }}
              </v-chip>
            </div>
          </div>
        </v-sheet>

        <!-- 原始讨论上下文 -->
        <v-sheet color="surface-light" rounded="lg" class="pa-4">
          <div class="text-subtitle-1 font-weight-medium mb-2">原始讨论</div>
          <div v-if="resource.originalMessage" class="original-message-context">
            <div class="d-flex">
              <v-avatar size="36" color="surface-variant" class="mt-1">
                <v-img :src="getAvatarUrl(resource.originalMessage.sender.avatarId)"></v-img>
              </v-avatar>
              <div class="ml-3">
                <div class="d-flex align-center">
                  <span class="font-weight-medium">{{ resource.originalMessage.sender.nickname }}</span>
                  <span class="text-caption text-medium-emphasis ml-2">
                    {{ formatMessageTime(resource.originalMessage.createdAt) }}
                  </span>
                </div>
                <p class="text-body-2 mt-1">{{ resource.originalMessage.content }}</p>
              </div>
            </div>
            <!-- 频道已退役（都归项目）：原始消息就地展示，不再提供跳转。 -->
          </div>
          <div v-else class="text-center py-4 text-body-2 text-medium-emphasis">没有关联的原始讨论信息</div>
        </v-sheet>
      </v-card-text>

      <v-divider></v-divider>

      <v-card-actions class="pa-4">
        <v-spacer></v-spacer>
        <v-btn
          v-if="canEditKnowledge(resource, ownerId)"
          color="error"
          variant="text"
          @click="emit('delete', resource)"
        >
          删除资料
        </v-btn>
        <v-btn color="primary" variant="tonal" @click="emit('openLink', resource)">
          <v-icon start>mdi-open-in-new</v-icon>
          打开资料
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<style scoped lang="scss">
.resource-info-row {
  margin-bottom: 8px;

  &:last-child {
    margin-bottom: 0;
  }
}

.resource-info-label {
  min-width: 80px;
  color: var(--muted);
  margin-right: 16px;
}

.document-preview,
.link-preview {
  min-height: 160px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
}

.rich-text-preview :deep(.tiptap-editor) {
  background-color: transparent;
  padding: 0;

  .ProseMirror {
    padding: 0;
    min-height: auto !important;
  }
}

.resource-video,
.resource-audio {
  max-width: 100%;
}
</style>
