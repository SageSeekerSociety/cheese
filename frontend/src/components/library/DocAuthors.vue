<script setup lang="ts">
// 一份文档的作者：并列的头像和名字，像 git 提交的 co-author。作者在文档建成那一刻定下
// （`lib/docAuthors.ts`），之后谁再改都不变；最近谁改的看修改记录。
import type { DocAuthor } from '@/lib/docAuthors'

import CheeseAvatar from '@/components/CheeseAvatar.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'

withDefaults(defineProps<{ authors: DocAuthor[]; size?: number }>(), { size: 16 })
</script>

<template>
  <span v-if="authors.length" class="doc-authors">
    <span class="doc-authors__faces">
      <template v-for="a in authors" :key="a.handle">
        <CheeseAvatar v-if="a.agent" class="doc-authors__face" :size="size" :name="a.name" :handle="a.handle" />
        <UserAvatar
          v-else
          class="doc-authors__face"
          :size="size"
          :name="a.name"
          :seed="a.handle"
          :avatar="a.avatar ?? ''"
        />
      </template>
    </span>
    <span v-for="a in authors" :key="a.handle" class="doc-authors__name">{{ a.name }}</span>
  </span>
</template>

<style scoped>
.doc-authors {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.doc-authors__faces {
  display: inline-flex;
  flex: none;
}

/* 叠着放：后一张压住前一张的一角，描一圈底色把两张分开。 */
.doc-authors__face + .doc-authors__face {
  margin-left: -4px;
  box-shadow: 0 0 0 1.5px var(--surface);
  border-radius: 50%;
}

.doc-authors__name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
