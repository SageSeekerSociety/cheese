<script setup lang="ts">
// 网页视图：把后端渲染好的那一页交给一个沙箱 iframe。
//
// 用 `srcdoc` 而不是 blob 地址。这一页是从应用自己的源上发出来的，而 blob 地址在
// 「不让同源的沙箱帧」里能不能载入，各家浏览器说法不一（blob 地址按发出来的那个源
// 归档，沙箱帧是不透明源）——交付的东西不该压在一个我验证不了的浏览器行为上。
// srcdoc 是一段文本，没有这一层疑问；两者送的字节是同一份，srcdoc 存的就是原文，
// 不经转义。代价是父页面的 CSP 会被 srcdoc 继承，而应用自己现在没有设。
//
// sandbox 里**不能有 allow-same-origin**。这一页是房间里的文档，不是应用自己的代码：
// 给了同源，它的脚本就能读会话里的东西。只给 allow-scripts——不给就没有脚本可跑，而
// 表格的多工作表标签正是靠页面自己的脚本切换的。网络由页面自带的 CSP meta 关着
// （见后端 `attachment_as_html`），沙箱管不着网络。
import { t } from '../../../i18n'

const props = defineProps<{ html: string | null }>()
</script>

<template>
  <!-- 换页就是换 srcdoc：这个属性一动，浏览器自己会重新载入这一帧，上面那一份不
       会留在里面。 -->
  <iframe
    v-if="props.html"
    class="page-frame"
    :srcdoc="props.html"
    sandbox="allow-scripts"
    :title="t('work.room.preview.webView')"
  />
</template>

<style scoped>
.page-frame {
  flex: 1 1 auto;
  width: 100%;
  min-width: 0;
  min-height: 0;
  border: 0;
  background: var(--surface);
}
</style>
