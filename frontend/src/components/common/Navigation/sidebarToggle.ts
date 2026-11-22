import { ref } from 'vue'

// 宽档里侧栏收起后，展开它的那颗按钮画在这一页页头的最左（PageSidebarToggle.vue）。这里数
// 眼下屏幕上有几个页头画着它：一个都没有的页面（没有用共用页头的页），顶栏最左补一颗
// （AppBar.vue），收起的侧栏在哪一页都展得开。
export const pageSidebarToggles = ref(0)
