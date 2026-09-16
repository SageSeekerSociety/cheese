<script setup lang="ts">
/**
 * 「本轮运行时间可能较长，完成后通知你」——问推送权限的那一刻（#1084 第 5 步）。
 *
 * **为什么不在首屏问。** 首屏上这个人还不知道为什么要授权，多数会拒；而浏览器被拒
 * 一次之后基本问不了第二次（`Notification.permission` 变成 `denied`，再调
 * `requestPermission` 直接返回 denied，不弹窗）。一次问坏，这个渠道对这个人就永久
 * 关闭了。
 *
 * **所以等到它自己说得通的那一刻**：这个人第一次真的遇到一轮跑过一分钟。那时他正
 * 等着结果，而「完成后通知你」是一句他当下就听得懂的话。
 *
 * 一个浏览器只问一次：答应了不用再问，「暂不开启」是这个人的决定，别追着他问第二
 * 次 —— 追问是人关掉一个渠道的头号原因，#1084 为通知定的那条规矩在这里同样成立。
 */
import { onUnmounted, ref, watch } from 'vue'

import { enablePush, permissionSettled, pushAvailable, pushSupported } from '@/services/webPush'

const props = defineProps<{
  /** 芝士此刻是不是在这个房间里运行（TopicView 按轮次生命周期给）。 */
  working: boolean
}>()

//: 一轮跑过这么久才问。#1084：等到这个人第一次遇到「这一轮不是几秒就完」。
const ASK_AFTER_MS = 60_000
//: 问过就不再问 —— 答应了、或者他说了暂不开启。
const ASKED_KEY = 'cheese:push-asked'

const shown = ref(false)
const busy = ref(false)
let timer: ReturnType<typeof setTimeout> | null = null

function asked(): boolean {
  try {
    return localStorage.getItem(ASKED_KEY) === '1'
  } catch {
    // 隐私窗口和禁用站点数据会抛。那种环境下推送本来也留不住，当作问过。
    return true
  }
}

function remember() {
  try {
    localStorage.setItem(ASKED_KEY, '1')
  } catch {
    // 写不进去就只在这一次会话里生效；下次进来最多再问一次，不会连着问。
  }
}

function stopTimer() {
  if (timer !== null) clearTimeout(timer)
  timer = null
}

watch(
  () => props.working,
  (working) => {
    stopTimer()
    if (!working || shown.value) return
    if (!pushSupported() || permissionSettled() || asked()) return
    timer = setTimeout(async () => {
      // 一分钟到了再问一次这个部署开不开推送：这一步要打后端，放在计时之前等于每
      // 开一轮就白问一次。
      if (props.working && (await pushAvailable())) shown.value = true
    }, ASK_AFTER_MS)
  },
  // `immediate` 是必须的：打开一个**已经在跑**的房间时，`working` 一进来就是 true
  // 而此后不再变化，靠变化触发的话这一格永远不会计时 —— 而「打开时它已经在跑」正
  // 是人回到一轮长活上的常态。
  { immediate: true }
)

onUnmounted(stopTimer)

async function accept() {
  busy.value = true
  // 先记下问过了，再去问权限：不论浏览器那一步是同意还是拒绝，这一次「问」都已经
  // 发生了，而拒绝恰恰是最不能重来的那种结果。
  remember()
  await enablePush()
  busy.value = false
  shown.value = false
}

function decline() {
  remember()
  shown.value = false
}
</script>

<template>
  <div v-if="shown" class="push-ask">
    <span class="push-ask__text">本轮运行时间可能较长，完成后通知你</span>
    <div class="push-ask__actions">
      <v-btn variant="text" size="small" color="primary" :loading="busy" @click="accept"> 开启通知 </v-btn>
      <v-btn variant="text" size="small" :disabled="busy" @click="decline">暂不开启</v-btn>
    </div>
  </div>
</template>

<style scoped>
.push-ask {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 8px 12px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--surface);
}

.push-ask__text {
  color: var(--text);
  font-size: 13px;
}

.push-ask__actions {
  display: flex;
  align-items: center;
  gap: 4px;
}
</style>
