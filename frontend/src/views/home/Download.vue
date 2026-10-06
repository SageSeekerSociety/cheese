<template>
  <DownloadView :logged-in="loggedIn" :downloads="downloads" :primary="primary" :version="version" :days="days" />
</template>

<script setup lang="ts">
// 容器：问这台电脑该下哪个版本、问更新日志、读会话；画面交给 DownloadView。
import type { Download } from '@/lib/desktop'
import type { ChangelogDay } from '@/lib/desktopChangelog'

import { computed, onMounted, ref } from 'vue'

import DownloadView from './DownloadView.vue'

import { downloadForThisComputer, DOWNLOADS } from '@/lib/desktop'
import { fetchDesktopRelease } from '@/lib/desktopChangelog'
import AccountService from '@/services/account'

// `DOWNLOADS` 是 `as const` 的一份只读清单，这一层和画面都不改它，所以照只读传。
const downloads: readonly Download[] = DOWNLOADS
const primary = ref<Download>(DOWNLOADS[0])
const version = ref<string | null>(null)
const days = ref<ChangelogDay[]>([])

const loggedIn = computed(() => AccountService.loggedIn)

onMounted(async () => {
  primary.value = await downloadForThisComputer()
  const release = await fetchDesktopRelease()
  version.value = release.version
  days.value = release.days
})
</script>
