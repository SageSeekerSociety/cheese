<template>
  <v-sheet flat rounded="lg">
    <v-toolbar :title="t('spaces.detail.selectTemplate.title')" color="transparent" density="compact">
      <template #prepend>
        <BaseButton kind="ghost" prepend-icon="mdi-chevron-left" @click="goBack">{{
          t('spaces.detail.selectTemplate.back')
        }}</BaseButton>
      </template>
    </v-toolbar>

    <v-list rounded="lg">
      <v-list-item
        prepend-icon="mdi-file-outline"
        :title="t('spaces.detail.selectTemplate.blankTemplate')"
        @click="selectTemplate(null)"
      ></v-list-item>
      <v-list-item
        v-for="(template, index) in templates"
        :key="index"
        :title="template.name"
        :subtitle="template.description"
        @click="selectTemplate(template)"
      >
        <template #prepend>
          <v-icon>mdi-file-document-outline</v-icon>
        </template>
      </v-list-item>
    </v-list>
  </v-sheet>
</template>

<script setup lang="ts">
import type { SpaceTaskTemplate } from '@/types'

import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'

import BaseButton from '@/components/base/BaseButton.vue'
import { stepBack } from '@/lib/backOut'
import { useSpaceStore } from '@/stores/space'

const router = useRouter()
const route = useRoute()
const spaceId = Number(route.params.spaceId)

const spaceStore = useSpaceStore()
const { templates } = storeToRefs(spaceStore)

const { t } = useI18n()

// 往回走：身后有应用内来路就退一格，没有（贴链接直接开这一页）就去这条路声明好的
// 上一级（router/spaces.ts 里 `meta.backTo`），而不是把一颗按下去没反应的按钮留在那。
const goBack = () => {
  stepBack(router, { name: 'SpacesDetailTasksList', params: { spaceId } })
}

const selectTemplate = (template: SpaceTaskTemplate | null) => {
  router.push({
    name: 'SpacesDetailPublishTask',
    params: { spaceId },
    query: { templateId: template ? templates.value.indexOf(template).toString() : 'blank' },
  })
}
</script>
