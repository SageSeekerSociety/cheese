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
import { ref } from 'vue'

import { canPromptInstall, detectIos, isInstalled, promptInstall } from '@/lib/pwaInstall'

const ios = detectIos()
const installing = ref(false)
const result = ref('')

async function install() {
  if (installing.value) return
  installing.value = true
  result.value = ''
  try {
    const outcome = await promptInstall()
    if (outcome === 'accepted') result.value = '已交给系统安装，稍后主屏幕上会出现芝士。'
    else if (outcome === 'dismissed') result.value = '这次取消了。想装的时候可以再用下面的方法。'
    else result.value = '这个浏览器这次没有给出安装入口，用下面的方法手动装。'
  } finally {
    installing.value = false
  }
}
</script>

<template>
  <v-card title="安装到手机" rounded="lg">
    <template #text>
      <p class="mb-4 text-body-2">
        装到主屏幕上，芝士就是一个独立的应用：有自己的图标，打开没有浏览器的地址栏，已看过的内容离线也打得开。
      </p>

      <v-alert v-if="isInstalled" type="success" variant="tonal" density="compact" class="mb-4">
        这台设备已经装好了——你现在就是以应用形态在使用芝士。
      </v-alert>

      <template v-else>
        <v-btn v-if="canPromptInstall" color="primary" :loading="installing" @click="install"> 安装到这台设备 </v-btn>
        <p v-if="result" class="text-body-2 mt-3">{{ result }}</p>
      </template>

      <div v-if="!isInstalled" class="steps">
        <div class="steps__title">{{ ios ? '在 iPhone / iPad 上：' : '自己从浏览器菜单装：' }}</div>
        <ol v-if="ios" class="steps__list">
          <li>用 <strong>Safari</strong> 打开本页（别的 App 里内置的浏览器没有「添加到主屏幕」）。</li>
          <li>点屏幕底部中间的「分享」按钮。</li>
          <li>在菜单里选「添加到主屏幕」，再点右上角的「添加」。</li>
        </ol>
        <ol v-else class="steps__list">
          <li>用 <strong>Chrome</strong> 打开本页（三星手机上用三星浏览器也行）。</li>
          <li>点右上角的「⋮」。</li>
          <li>选「安装应用」——部分版本写的是「添加到主屏幕」。</li>
        </ol>
        <p class="text-body-2 mt-2 text-medium-emphasis">
          电脑上也可以用同样的方法：Chrome 地址栏右侧会出现一个带向下箭头的安装图标。
        </p>
      </div>

      <v-alert v-if="!isInstalled" type="warning" variant="tonal" density="compact" class="mt-4">
        <div class="font-weight-bold mb-1">请用 Chrome 装，别用「添加到桌面」的国产浏览器</div>
        <div class="text-body-2">
          国产浏览器、以及大多数只提供「添加到桌面」的浏览器，装出来的不是应用，是一个包着网页的壳：壳里选不了文件（发不了图、传不了文件），也收不到消息推送。用
          Chrome 装出来的才是真应用。
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
