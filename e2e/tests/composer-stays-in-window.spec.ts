import { expect, test, type Page } from '@playwright/test'

// 频道顶上「AI 现在在干什么」那条（`AgentNowStrip`）和下面的对话面板排在同一只
// 纵向 flex 列里（`TopicChatColumn` 的结构）。有队友在干活时那条才存在，而它一
// 出现，输入框曾经被顶到窗口下沿外面：对话面板还按「整栏那么高」算自己，两块加
// 起来比栏高出一整条，输入区又是面板里最后一项，于是连同它被裁掉——栏自己也不滚，
// 人看不到输入框。
//
// 量的是用户看得见的后果：输入框的底边不能低于窗口下沿。它跟面板怎么算自己的高
// 度是两回事——换个写法一样能修好，只要输入框还在窗口里，这条就绿。
//
// 时间线必须有内容。空时间线的面板压得下去，**有消息**的面板压不下去（里面的
// `.messages` 撑住了），这正是生产上的条件：那一段对话本来就有不少消息。单测的
// jsdom 不给盒模型，所以这条按仓库里「从 dev server 挂真组件、只替接口」的老办法
// （见 `work-panel-file-tab-width.spec.ts`），本文件也列进了 config 的 COMPONENT_SPECS。
const fixture = `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><body>
<div id="fixture"></div><script type="module">
import {createApp,h} from '/node_modules/.vite/deps/vue.js';
import {VApp} from '/node_modules/.vite/deps/vuetify_components.js';
import ChatPanel from '/src/components/ChatPanel.vue';
import AgentNowStrip from '/src/components/room/AgentNowStrip.vue';
import vuetify from '/src/plugins/vuetify.ts';
import i18n,{setLocale} from '/src/i18n/index.ts';
import '/src/style.css';
setLocale('zh-CN');

// 一位队友正在干活：这正是那块状态条出现、缺陷才现形的条件。
const now = Math.floor(Date.now()/1000);
const lines = [{handle:'h',name:'芝士',kind:'working',since:now-400,
  step:'执行命令 cd frontend && NODE="$(cat /tmp/nodepath.txt)/node"',lastFrameAt:now*1000-2000}];

// 一段够长的对话：面板里有内容才压不下去，和生产上一样。
const msgs = Array.from({length:40},(_,i)=>({id:'b'+i,topic_id:'tc-1',seq:i+1,kind:'message',
  author:'u',content:'第 '+(i+1)+' 行消息，用来把时间线填得比窗口还高。'.repeat(3),
  created_at:'2026-08-18T00:00:00Z',updated_at:'2026-08-18T00:00:00Z'}));

const topic = {id:'tc-1',project_id:'p1',parent_id:null,title:'t',kind:'topic',status:'active',
  created_by:'u',created_at:'2026-08-18T00:00:00Z',updated_at:'2026-08-18T00:00:00Z'};

// 只替接口：没有后端，也不连长连接。这一页断的是布局，不是取数。
const json = d => new Response(JSON.stringify({code:200,data:d}),{headers:{'Content-Type':'application/json'}});
window.fetch = async url => String(url).includes('/blocks')
  ? json({data:msgs,total:msgs.length,has_more:false,oldest_id:'b0',has_newer:false,newest_id:'b39',members:[]})
  : json({data:[],total:0});
window.WebSocket = class { constructor(){ setTimeout(()=>this.onopen&&this.onopen(),0) }
  close(){} send(){} addEventListener(){} removeEventListener(){} };

// 和生产同一根高度链：定高的页面 → 两栏那一排 → 对话这一栏。
const rootStyle = {height:'100vh',display:'flex',flexDirection:'column',minHeight:'0'};
const panesStyle = {flex:'1 1 auto',minHeight:'0',display:'flex'};
const colStyle = {flex:'0 0 60%',minHeight:'0',display:'flex',flexDirection:'column'};

createApp({setup(){return()=>h(VApp,{},{default:()=>h('div',{style:rootStyle},[
  h('div',{class:'panes',style:panesStyle},[
    h('div',{class:'chat-col col col-chat',style:colStyle},[
      h(AgentNowStrip,{lines}),
      h(ChatPanel,{topic,showComposer:true,hideHeader:true,members:[],topicList:[]}),
    ]),
  ]),
])})}}).use(vuetify).use(i18n).mount('#fixture');
</script></body></html>`

/** 输入框底边比窗口下沿低多少 px。0 或负数表示整只都在窗口里。 */
function composerOverhang(page: Page) {
  return page.evaluate(() => {
    const composer = document.querySelector<HTMLElement>('.composer')
    if (!composer) throw new Error('页面上没有输入框')
    return Math.round(composer.getBoundingClientRect().bottom - innerHeight)
  })
}

test('队友在干活时，输入框仍然完整地留在窗口里', async ({ page }) => {
  await page.route('**/__strip-over-composer__', (route) =>
    route.fulfill({ contentType: 'text/html', body: fixture })
  )
  await page.goto('/__strip-over-composer__', { waitUntil: 'commit' })

  // 先确认条件成立：状态条真在、输入框真在、时间线真填了东西。缺一样，下面的量法就没意义。
  await expect(page.locator('.agent-now')).toBeVisible({ timeout: 60_000 })
  await expect(page.locator('.composer')).toBeVisible({ timeout: 60_000 })
  await expect(page.locator('.messages')).toBeVisible({ timeout: 60_000 })

  // 几种常用的窗口高矮都量一遍：状态条占多少、窗口多高都不该把输入框挤出去。
  for (const height of [800, 600, 480]) {
    await page.setViewportSize({ width: 1200, height })
    await expect.poll(() => composerOverhang(page), { message: `窗口高 ${height}` }).toBeLessThanOrEqual(0)
  }
})
