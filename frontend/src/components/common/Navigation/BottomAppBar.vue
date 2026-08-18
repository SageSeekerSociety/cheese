<template>
  <v-bottom-navigation :elevation="0" bg-color="background" grow>
    <!-- 收到的就是手机那份清单（destinations.ts 的 tabItems），这里不再过滤：
         底栏装什么是清单的事，不是渲染的事。 -->
    <v-btn v-for="item in items" :key="item.key" :to="item.to" @click="!item.to && item.action?.()">
      <v-icon v-if="item.icon">{{ item.icon }}</v-icon>
      <v-avatar v-else-if="item.img" size="24">
        <v-img :src="item.img"></v-img>
      </v-avatar>
      <span>{{ item.title }}</span>
    </v-btn>
  </v-bottom-navigation>
</template>

<script setup lang="ts">
import { toRefs } from 'vue'

import { NavItem } from './types'

const navBarProps = withDefaults(defineProps<{ items: NavItem[] }>(), {
  items: () => [],
})

const { items } = toRefs(navBarProps)
</script>

<style lang="scss"></style>
