<!--
  「安装到手机」。

  入口本身很低调（用户要的一格）——真正要解决的问题是：在 Android 上，**从哪个
  浏览器装**决定了装出来的是不是应用。

  带 GMS 的 Chrome（和三星设备上的 Samsung Internet）会把 PWA 装成 WebAPK：一个
  真正的应用条目，文件选择器、推送、离线都跟着走。其它浏览器的「添加到桌面」装的
  往往是快捷方式 / 包着 WebView 的壳——**壳里的 input[type=file] 弹不出来**（宿主
  没实现 onShowFileChooser），推送也收不到。2026-09-17 真机实测过：同一条上传路径，
  浏览器里能弹选择器、从国产浏览器装到主屏的那个壳里不能。

  所以这一页的顺序是：能给按钮就给按钮（Chromium 的 beforeinstallprompt），给不了
  就写清楚「用哪个浏览器、点哪几下」，最后把「别用国产浏览器装」这条单独拎出来——
  它不是免责声明，是这块最容易踩的坑。
-->
<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { canPromptInstall, detectIos, isInstalled, promptInstall } from '@/lib/pwaInstall'

const { t } = useI18n()

const ios = detectIos()
const installing = ref(false)
// 存结果而不是存句子：切语言时这句话要跟着变，存下来的字符串不会。
const outcome = ref<'accepted' | 'dismissed' | 'noPrompt' | ''>('')

const result = computed(() => {
  switch (outcome.value) {
    case 'accepted':
      return t('users.settings.install.resultAccepted')
    case 'dismissed':
      return t('users.settings.install.resultDismissed')
    case 'noPrompt':
      return t('users.settings.install.resultNoPrompt')
    default:
      return ''
  }
})

async function install() {
  if (installing.value) return
  installing.value = true
  outcome.value = ''
  try {
    const promptOutcome = await promptInstall()
    if (promptOutcome === 'accepted') outcome.value = 'accepted'
    else if (promptOutcome === 'dismissed') outcome.value = 'dismissed'
    else outcome.value = 'noPrompt'
  } finally {
    installing.value = false
  }
}
</script>

<template>
  <v-card :title="t('users.settings.install.title')" rounded="lg">
    <template #text>
      <p class="mb-4 text-body-2">
        {{ t('users.settings.install.intro') }}
      </p>

      <v-alert v-if="isInstalled" type="success" variant="tonal" density="compact" class="mb-4">
        {{ t('users.settings.install.installed') }}
      </v-alert>

      <template v-else>
        <v-btn v-if="canPromptInstall" color="primary" :loading="installing" @click="install">
          {{ t('users.settings.install.installButton') }}
        </v-btn>
        <p v-if="result" class="text-body-2 mt-3">{{ result }}</p>
      </template>

      <div v-if="!isInstalled" class="steps">
        <div class="steps__title">
          {{ ios ? t('users.settings.install.stepsTitleIos') : t('users.settings.install.stepsTitleOther') }}
        </div>
        <ol v-if="ios" class="steps__list">
          <i18n-t keypath="users.settings.install.iosStepOpen" scope="global" tag="li">
            <template #browser><strong>Safari</strong></template>
          </i18n-t>
          <li>{{ t('users.settings.install.iosStepShare') }}</li>
          <li>{{ t('users.settings.install.iosStepAdd') }}</li>
        </ol>
        <ol v-else class="steps__list">
          <i18n-t keypath="users.settings.install.otherStepOpen" scope="global" tag="li">
            <template #browser><strong>Chrome</strong></template>
          </i18n-t>
          <li>{{ t('users.settings.install.otherStepMenu') }}</li>
          <li>{{ t('users.settings.install.otherStepInstall') }}</li>
        </ol>
        <p class="text-body-2 mt-2 text-medium-emphasis">
          {{ t('users.settings.install.desktopNote') }}
        </p>
      </div>

      <v-alert v-if="!isInstalled" type="warning" variant="tonal" density="compact" class="mt-4">
        <div class="font-weight-bold mb-1">{{ t('users.settings.install.warningTitle') }}</div>
        <div class="text-body-2">
          {{ t('users.settings.install.warningBody') }}
        </div>
      </v-alert>
    </template>
  </v-card>
</template>

<style scoped>
.steps {
  margin-top: 20px;
}
.steps__title {
  margin-bottom: 6px;
  font-size: 14px;
  font-weight: 600;
}
.steps__list {
  padding-left: 20px;
  font-size: 14px;
  line-height: 1.9;
}
</style>
