<template>
  <v-avatar
    :size="size"
    :style="hasAvatar ? undefined : { backgroundColor: fallbackColor }"
    :aria-hidden="isDecorative ? 'true' : undefined"
    :role="isDecorative ? undefined : 'img'"
    :aria-label="isDecorative ? undefined : alt"
  >
    <v-img v-if="hasAvatar" :src="avatar" @error="onAvatarError">
      <!-- Real avatar failed to load → colored initial, not a broken tile. -->
      <template #error>
        <span class="user-avatar-char" :style="{ backgroundColor: fallbackColor }">{{ initial }}</span>
      </template>
    </v-img>
    <span v-else class="user-avatar-char">{{ initial }}</span>
  </v-avatar>
</template>

<script setup lang="ts">
// 真头像加载失败时的两处收尾，都落在这个文件里：
//
// 1. 「这个 URL 取不到」要被记住（`utils/avatarFailures`），否则每切回一次列表页，
//    每个没有图片文件的 id 都会再发一次必然 404 的请求。记忆的代价写在那个模块里。
// 2. 记住之后当帧**不能**改 `hasAvatar`：那会把 `v-img` 整个拆掉重排，而 `v-img`
//    自己的 error 插槽已经把首字母画好了，留在原地更稳。`hasAvatar` 读的是普通
//    `Set`，不会因为记忆被写入而重算 —— 它只在组件新建（或 `avatar` 变了）时重判
//    一次，所以「直接走首字母」发生在第二次进来，而不是失败的那一帧。
//
// 不写 `loading="lazy"`：`v-img` 内部本来就挂了 `v-intersect`（`once` 的
// IntersectionObserver），进视口才 init，再加一层是重复劳动。
//
// 关于对读屏隐身：头像在这里**默认是装饰**（`aria-hidden`），因为 grep 出来的每一处
// 调用方（名册、评论、反馈、成员列表……）都把头像放在一个可见的 handle / 昵称旁边，
// 那个名字才是身份，头像再念一遍是噪音。这**不是**「头像永远不该被听见」——头像是
// 唯一身份表示的地方（比如未来只有头像没有文字的格子）必须传 `alt`，那时根节点换成
// `role="img"` + `aria-label`，图加载不出来也会念同一个名字，不会忽然变哑。默认值与
// `alt` 是同一个开关，就是为了让调用方拿主意，而不是组件替所有人决定。
import { computed } from 'vue'

import { avatarColor, avatarInitial } from '@/utils/avatar'
import { isAvatarKnownFailed, rememberAvatarFailure } from '@/utils/avatarFailures'

const {
  avatar = '',
  name = '',
  alt = '',
  size = 48,
} = defineProps<{
  /** URL of an uploaded avatar image. Empty → colored-initial fallback. */
  avatar?: string
  /** Display name / handle used for the initial + deterministic color. */
  name?: string
  /**
   * 头像对读屏读出来的名字。留空 = 装饰性（默认），旁边有名字可读；只有在头像
   * 自己是这一格唯一身份时才传。
   */
  alt?: string
  size?: string | number
}>()

// 已知取不到的 URL 直接当「没有图」，不再造 `v-img` 去问一次。
const hasAvatar = computed(() => !!avatar && !isAvatarKnownFailed(avatar))
const initial = computed(() => avatarInitial(name))
const fallbackColor = computed(() => avatarColor(name))
const isDecorative = computed(() => !alt)

function onAvatarError() {
  rememberAvatarFailure(avatar)
}
</script>

<style scoped>
.user-avatar-char {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
  line-height: 1;
  /* 这个 #fff 是故意保留的写死值，别"顺手修好"它：底色是 avatarColor() 算出来的
     hsl(色相, 55%, 55%)，两套主题下同一个值，所以压在上面的字也不该随主题变。
     换成 var(--ink)/on-surface 会在深色下变成浅灰压浅底，反而更糟。
     （另注：这个底色本身在黄绿色相段上对白字只有约 1.7:1，两个主题都读不清——
      那是 avatarColor() 的问题，不属于深色适配，另有一张卡处理。） */
  color: #fff;
  font-weight: 600;
}
</style>
