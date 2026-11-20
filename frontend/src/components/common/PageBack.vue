<script setup lang="ts">
// 桌面上一页的 ←：画在这一页自己页头的最左边（顶栏上不再有它）。按钮本身是外壳交进来的
// （lib/pageChrome.ts 的 `back`，即 ParentBackButton）：它要读路由和项目，页头一 import 它，
// 每一处用页头的画面就不再能只靠 props 画出来。没有外壳、或手机上（← 在手机顶栏里）不画。
import { inject } from 'vue'
import { useDisplay } from 'vuetify'

import { EMPTY_PAGE_CHROME, PAGE_CHROME } from '@/lib/pageChrome'

const { back } = inject(PAGE_CHROME, EMPTY_PAGE_CHROME)
const { mdAndUp } = useDisplay()
</script>

<template>
  <component :is="back" v-if="mdAndUp && back" class="page-back" />
</template>
