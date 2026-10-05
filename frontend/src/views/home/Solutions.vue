<template>
  <SolutionsView :logged-in="loggedIn" />
</template>

<script setup lang="ts">
// 容器：只把「登录没登录」这一件会变的事从会话里读出来，画面交给 SolutionsView。
import { computed } from 'vue'

import SolutionsView from './SolutionsView.vue'

import AccountService from '@/services/account'

const loggedIn = computed(() => AccountService.loggedIn)
</script>
