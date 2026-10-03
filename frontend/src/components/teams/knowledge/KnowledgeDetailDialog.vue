<script setup lang="ts">
// 资料详情：预览（图 / 视频 / 音频 / 文档 / 富文本 / 链接 / 代码）+ 资料信息 + 原始讨论。
//
// 三块都不判断「这一条归谁」：能不能删由页算好 `ownerId` 递进来，删除本身报上去
// （`delete`），由页去问那一句再打接口 —— 组件这一层不弹确认框、不发请求。
//
// `.code-block` 的样式就在这一件里 —— 弹窗传送出去以后，页那一层的 `:deep`
// 够不着它（理由和那条规则写在一起，见文件末尾的样式块）。色值是
// `src/style.css` 的 `--code-bg` / `--code-ink`。
import type { Knowledge, KnowledgeContentData } from '@/types'
import type { AudioMeta, FileMeta, ImageMeta, VideoMeta } from '@/types/materials'

import { getAvatarUrl } from '@/utils/materials'

import BaseButton from '@/components/base/BaseButton.vue'
import TipTapViewer from '@/components/common/Editor/TipTapViewer.vue'
import { t } from '@/i18n'
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

defineProps<{
  modelValue: boolean
  resource: Knowledge | null
  content: KnowledgeContentData
  ownerId?: number
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  openLink: [resource: Knowledge]
  delete: [resource: Knowledge]
}>()
</script>

<template>
  <v-dialog :model-value="modelValue" max-width="700" @update:model-value="emit('update:modelValue', $event)">
    <v-card v-if="resource" class="resource-detail-card">
      <v-card-title class="d-flex justify-space-between align-center pa-4">
        <div>{{ resource.name }}</div>
        <BaseButton
          icon="mdi-close"
          :aria-label="t('navigation.shell.close')"
          @click="emit('update:modelValue', false)"
        />
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
            {{ t('teams.knowledge.videoUnsupported') }}
          </video>

          <!-- 音频预览 -->
          <audio
            v-else-if="resource.type === 'MATERIAL' && resource.material?.type === 'audio'"
            controls
            class="mx-auto resource-audio d-block my-4"
            style="width: 100%"
          >
            <source :src="resource.material.url" />
            {{ t('teams.knowledge.audioUnsupported') }}
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
                {{ (resource.material.meta as FileMeta).name || t('teams.knowledge.documentExternal') }}
              </div>
              <div class="text-caption text-medium-emphasis mb-3">
                {{ formatFileSize((resource.material.meta as FileMeta).size) }}
              </div>
              <BaseButton kind="secondary" size="sm" @click="emit('openLink', resource)">{{
                t('teams.knowledge.openDocument')
              }}</BaseButton>
            </div>
          </v-sheet>

          <!-- 富文本内容预览 -->
          <v-sheet v-else-if="resource.type === 'TEXT' && content.richText" class="pa-4 text-preview rounded-lg">
            <div class="rich-text-preview">
              <TipTapViewer :value="content.richText" />
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
                <BaseButton kind="secondary" size="sm" @click="emit('openLink', resource)">{{
                  t('teams.knowledge.visitLink')
                }}</BaseButton>
              </div>
            </div>
          </v-sheet>

          <!-- 代码片段预览 -->
          <v-sheet v-else-if="resource.type === 'CODE'" color="surface-light" class="pa-4 code-preview rounded-lg">
            <div class="d-flex align-center mb-2">
              <div class="text-subtitle-1 font-weight-medium">{{ t('teams.knowledge.typeCode') }}</div>
              <v-chip v-if="content.language" class="ml-2" size="small" variant="tonal">
                {{ content.language }}
              </v-chip>
            </div>
            <!-- 底色不写在这里：代码块是【故意反色】的元素，两个主题两套值，
                 是 src/style.css 的 --code-bg / --code-ink，见 <style> 里的
                 .code-block -->
            <v-sheet class="pa-4 rounded-lg code-block">
              <pre class="language-{{ content.language || 'javascript' }}"><code>{{ content.code }}</code></pre>
            </v-sheet>
          </v-sheet>

          <div v-else class="text-center py-4">
            <v-icon :icon="resourceTypeIcon(resource.type, resource.material?.type)" size="64"></v-icon>
            <div class="mt-2 text-body-2">{{ t('teams.knowledge.noPreview') }}</div>
          </div>
        </div>

        <!-- 资料信息 -->
        <v-sheet color="surface-light" rounded="lg" class="pa-4 mb-4">
          <div class="text-subtitle-1 font-weight-medium mb-2">{{ t('teams.knowledge.resourceInfo') }}</div>
          <div class="d-flex resource-info-row">
            <div class="resource-info-label">{{ t('teams.knowledge.type') }}</div>
            <div>{{ resourceTypeName(resource.type, resource.material?.type) }}</div>
          </div>
          <div v-if="resource.material?.type === 'file'" class="d-flex resource-info-row">
            <div class="resource-info-label">{{ t('teams.knowledge.fileName') }}</div>
            <div>{{ (resource.material.meta as FileMeta).name }}</div>
          </div>
          <div v-if="resource.material" class="d-flex resource-info-row">
            <div class="resource-info-label">{{ t('teams.knowledge.size') }}</div>
            <div>{{ formatFileSize(resource.material.meta.size) }}</div>
          </div>
          <div
            v-if="resource.material?.type === 'video' || resource.material?.type === 'audio'"
            class="d-flex resource-info-row"
          >
            <div class="resource-info-label">{{ t('teams.knowledge.duration') }}</div>
            <div>{{ formatDuration((resource.material.meta as VideoMeta | AudioMeta).duration) }}</div>
          </div>
          <div class="d-flex resource-info-row">
            <div class="resource-info-label">{{ t('teams.knowledge.creator') }}</div>
            <div class="d-flex align-center">
              <v-avatar size="24" rounded="circle" color="surface-variant" class="mr-2">
                <v-img :src="getAvatarUrl(resource.creator.avatarId)"></v-img>
              </v-avatar>
              <span>{{ resource.creator.nickname }}</span>
            </div>
          </div>
          <div class="d-flex resource-info-row">
            <div class="resource-info-label">{{ t('teams.knowledge.createdAt') }}</div>
            <div>{{ formatDetailDate(resource.createdAt) }}</div>
          </div>
          <div class="d-flex resource-info-row">
            <div class="resource-info-label">{{ t('teams.knowledge.sourceChannel') }}</div>
            <div>{{ resource.sourceChannel?.name || t('teams.knowledge.unknownChannel') }}</div>
          </div>
          <div v-if="resource.description" class="d-flex resource-info-row">
            <div class="resource-info-label">{{ t('teams.knowledge.description') }}</div>
            <div>{{ resource.description }}</div>
          </div>
          <div v-if="resource.labels && resource.labels.length > 0" class="d-flex resource-info-row">
            <div class="resource-info-label">{{ t('teams.knowledge.tags') }}</div>
            <div class="d-flex flex-wrap">
              <v-chip v-for="tag in resource.labels" :key="tag" size="small" variant="tonal" class="mr-1 mb-1">
                {{ tag }}
              </v-chip>
            </div>
          </div>
        </v-sheet>

        <!-- 原始讨论上下文 -->
        <v-sheet color="surface-light" rounded="lg" class="pa-4">
          <div class="text-subtitle-1 font-weight-medium mb-2">{{ t('teams.knowledge.originalDiscussion') }}</div>
          <div v-if="resource.originalMessage" class="original-message-context">
            <div class="d-flex">
              <v-avatar size="36" rounded="circle" color="surface-variant" class="mt-1">
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
          <div v-else class="text-center py-4 text-body-2 text-medium-emphasis">
            {{ t('teams.knowledge.noOriginalDiscussion') }}
          </div>
        </v-sheet>
      </v-card-text>

      <v-divider></v-divider>

      <v-card-actions class="pa-4">
        <v-spacer></v-spacer>
        <BaseButton v-if="canEditKnowledge(resource, ownerId)" kind="ghost" @click="emit('delete', resource)">
          {{ t('teams.knowledge.deleteResource') }}
        </BaseButton>
        <BaseButton kind="primary" @click="emit('openLink', resource)">
          <v-icon start>mdi-open-in-new</v-icon>
          {{ t('teams.knowledge.openResource') }}
        </BaseButton>
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

/* 代码块：故意反色的元素，两块颜色是 `src/style.css` 里的 `--code-bg` /
   `--code-ink`（浅色主题深底浅字，深色主题换成比 surface 亮一档的
   --fill-2 + --text，见那两个 token 的注释）。

   这条规则**必须**长在这一件里，不能写回页上当 `:deep(.code-block)`：弹窗
   是 `v-dialog`，内容被传送到 `body`，页那一层的 `:deep` 编译出来是
   `[data-v-页] .code-block` —— 传送之后 body 里没有任何祖先带页的作用域
   属性，规则一条也命中不了（`Knowledge.spec.ts` 里有一格把这件事钉住了）。 */
.code-block {
  overflow-x: auto;
  color: var(--code-ink);
  background-color: var(--code-bg);
  font-family: 'Fira Code', monospace;
  font-size: 0.9rem;
  line-height: 1.5;

  pre {
    margin: 0;
  }
}

.resource-video,
.resource-audio {
  max-width: 100%;
}
</style>
