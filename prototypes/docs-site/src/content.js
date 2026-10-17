const I = {
  book:'<path d="M4 19.5V5a2 2 0 0 1 2-2h13v16H6.5A2.5 2.5 0 0 0 4 21.5v-2Z"/><path d="M8 7h7"/>',
  code:'<path d="m8 8-5 4 5 4M16 8l5 4-5 4M14 4l-4 16"/>',
  api:'<path d="M4 7h16M4 12h10M4 17h7"/><circle cx="18" cy="16" r="3"/>',
  log:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  arrow:'<path d="M5 12h14M13 6l6 6-6 6"/>',
  rocket:'<path d="M5 15c-1.5 1.3-2 4-2 6 2 0 4.7-.5 6-2M14 4c3-1 6-1 6-1s0 3-1 6l-6 6-5-5 6-6Z"/><circle cx="15" cy="9" r="1.5"/>',
  chat:'<path d="M21 12a8 8 0 0 1-11.8 7L4 20l1.1-4.6A8 8 0 1 1 21 12Z"/>',
  check:'<path d="M20 6 9 17l-5-5"/>',
  users:'<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0M16 4.5a3.5 3.5 0 0 1 0 7M21.5 20a6.5 6.5 0 0 0-4-6"/>',
  folder:'<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/>',
  cpu:'<rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4"/>',
  layers:'<path d="m12 3 9 5-9 5-9-5 9-5Z"/><path d="m3 13 9 5 9-5"/>',
  info:'<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
  bulb:'<path d="M9 18h6M10 21h4M12 3a6 6 0 0 0-3.5 10.9c.6.5 1 1.2 1 2.1h5c0-.9.4-1.6 1-2.1A6 6 0 0 0 12 3Z"/>',
  warn:'<path d="M12 3 2 20h20L12 3Z"/><path d="M12 10v4M12 17h.01"/>',
  copy:'<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/>',
  down:'<path d="m6 9 6 6 6-6"/>',
  md:'<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M7 15V9l2.5 3L12 9v6M17 9v6M15 13l2 2 2-2"/>',
  up:'<path d="M7 10v11M15 5.9 14 10h5.8a2 2 0 0 1 2 2.3l-1.4 7A2 2 0 0 1 18.4 21H7V10l4-8a2.5 2.5 0 0 1 4 3.9Z"/>',
  dn:'<path d="M17 14V3M9 18.1 10 14H4.2a2 2 0 0 1-2-2.3l1.4-7A2 2 0 0 1 5.6 3H17v11l-4 8a2.5 2.5 0 0 1-4-3.9Z"/>',
  pen:'<path d="M12 20h9M16.5 3.5a2.1 2.1 0 1 1 3 3L7 19l-4 1 1-4Z"/>',
  rss:'<path d="M4 11a9 9 0 0 1 9 9M4 4a16 16 0 0 1 16 16"/><circle cx="5" cy="19" r="1"/>',
  git:'<circle cx="6" cy="6" r="2.5"/><circle cx="6" cy="18" r="2.5"/><circle cx="18" cy="8" r="2.5"/><path d="M6 8.5v7M18 10.5c0 4-6 3-10.5 6"/>',
  list:'<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
  sun:'<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  moon:'<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z"/>',
  doc:'<path d="M4 5a2 2 0 0 1 2-2h9l5 5v11a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2Z"/><path d="M14 3v5h5"/>',
  hash:'<path d="M4 9h16M4 15h16M10 3 8 21M16 3l-2 18"/>',
  search:'<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
  spark:'<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M6 18l2.5-2.5M15.5 8.5 18 6"/>'
};
const ic=(n,s)=>`<svg class="ico" viewBox="0 0 24 24"${s?` style="${s}"`:''}>${I[n]}</svg>`;

/* ================= navigation ================= */
const NAV = {
  guide:{label:'使用文档', icon:'book', first:'quickstart', groups:[
    ['开始',[['quickstart','快速开始'],['working-with-cheese','怎么和芝士一起干活']]],
    ['基础',[['projects','项目'],['teams','团队'],['members','成员']]],
    ['干活',[['rooms','话题（房间）'],['agents','AI 队友'],['tasks','活与看板'],['accept','验收与采纳']]],
    ['产出',[['files','文件与下载',1],['sites','发布网站',1],['kb','知识库',1]]],
    ['资源',[['compute','设备与算力'],['inbox','通知与收件箱',1]]],
  ]},
  dev:{label:'开发文档', icon:'code', first:'overview', groups:[
    ['开始',[['overview','概览'],['workflows','本地开发'],['testing-without-docker','没有 docker 怎么跑测试']]],
    ['架构',[['spec','产品与实现总纲'],['where-a-turn-runs','一轮活跑在哪台机器上'],['remote-execution','中央会话与远程执行'],['what-the-agent-perceives','芝士能感知到什么'],['agent-liveness','一轮什么时候算卡住'],['agent-principles','平台上的 agent 原则']]],
    ['规范',[['api-conventions','API 约定'],['design-system','设计系统'],['i18n','中英双语与词条'],['chat-publication','芝士的发言规则']]],
    ['部署与运维',[['infrastructure','部署、备份与访问'],['device-self-hosting','设备自托管'],['microcloud','云机器（MicroCloud）']]],
    ['质量',[['evals','场景与验收清单'],['ci-feedback','CI 反馈度量']]],
  ]},
  api:{label:'API 参考', icon:'api', first:'overview', groups:[
    ['开始',[['overview','寻址与认证']]],
    ['端点',[['export','导出项目'],['accept','采纳验收卡']]],
  ]},
  changelog:{label:'更新日志', icon:'log', first:'', groups:[]}
};

/* ================= content ================= */
const INVITE_IMG='__IMG_INVITE__';
const P = {};
P['guide/quickstart']={title:'快速开始',lede:'知是是你和 AI 队友一起做项目的地方。这一篇带你走完三步——组队、建项目、开一个话题跟它说上第一句话，十分钟左右。',updated:'2026-09-24',src:'docs/manual/quickstart.md',body:`
<div class="callout note">${ic('info')}<p><strong>这一篇不假设你是来写代码的。</strong>很多人用知是只是和 AI 一起理事情、出方案、写东西。仓库怎么连、活怎么验收，等你需要时再看。</p></div>
<h2 id="steps">三步走完<a class="anchor" href="#steps">#</a></h2>
<div class="steps">
 <div class="step"><h3 id="team">组一个团队</h3>
  <p>在首页左侧栏点「团队」，再点顶部的「创建团队」，填好名称和描述。要把人拉进来，进团队后依次点「成员管理」→「邀请成员」，填对方的 UID——<strong>UID 在对方的头像菜单里</strong>。</p>
  <figure><div class="shot"><img src="${INVITE_IMG}" alt="「邀请成员」弹窗：填 UID、选角色（默认「普通成员」）、可选填一句邀请消息，右下角是「邀请」按钮"></div><figcaption>「邀请成员」弹窗：填 UID、选角色、可选一句邀请消息</figcaption></figure>
  <div class="callout tip">${ic('bulb')}<p>一个人用也可以：建项目时「所属团队」选<strong>「个人」</strong>，那是只有你自己的一个团队，规则完全一样。</p></div>
 </div>
 <div class="step"><h3 id="project">建一个项目</h3>
  <p>界面最左边那条竖栏是你的项目栏，底部的 <strong>＋</strong> 是新建项目。要填的只有两样：<strong>项目名称</strong>和<strong>所属团队</strong>。</p>
  <p>点「创建」，界面直接进入这个项目。项目自带一个放东西的地方——你和芝士做出来的文件都落在那儿。想接自己已有的 GitHub 仓库，见<a class="link" href="#/guide/projects#upstream">项目 · 连接你已有的仓库</a>，第一趟不用管。</p>
 </div>
 <div class="step"><h3 id="talk">开个话题，说一句人话</h3>
  <p>打开项目，第一屏是<strong>看板</strong>，不是聊天——看板答的是「现在该谁动」。左侧栏顶部的 <strong>＋（新建话题）</strong>开一个新房间，项目的默认队友已经在里面了。</p>
  <p>在底部输入框里，像跟同事说话那样说你想做什么，<strong>记得 @ 一下芝士</strong>——没有 @ 的消息它收不到：</p>
  <div class="bubble"><div class="avatar" style="background:#dfe7f5;color:#3a63a8">你</div><div><span class="mention">@芝士</span> 帮我看看这个项目现在有些什么，理一份清单给我。</div></div>
  <p>按回车。<strong>到这里就算走完了。</strong></p>
 </div>
</div>
<h2 id="what-happens">它回你之后<a class="anchor" href="#what-happens">#</a></h2>
<p>它会先回一句它理解的目标和第一步，然后开工。这句话值得读——理解偏了，现在说一声只损失几秒。开工之后房间里有三处在动：</p>
<div class="mini-grid">
 <div class="mini"><b>${ic('chat')}对话</b><p>它的判断，和要你拍板的选择。</p></div>
 <div class="mini"><b>${ic('list')}施工现场</b><p>每调用一次工具多一行，是它当下在做的事。</p></div>
 <div class="mini"><b>${ic('doc')}实况文档</b><p>这个房间的当前全貌，隔几天回来先看它。</p></div>
</div>
<div class="callout tip">${ic('bulb')}<p><strong>你随时可以插话</strong>，不用等它停下来——想改方向、想补一个条件，直接说就行。</p></div>
<h2 id="next">接下来<a class="anchor" href="#next">#</a></h2>
<p>剩下这些在你需要的时候再看，不用现在读。</p>
<div class="cards">
 <a class="card" href="#/guide/working-with-cheese"><div class="ci">${ic('spark')}</div><b>怎么和芝士一起干活</b><p>真正需要花点时间的是这个，不是按钮在哪。</p></a>
 <a class="card" href="#/guide/accept"><div class="ci">${ic('check')}</div><b>验收与采纳</b><p>它交东西给你时会出现一张验收卡，采纳就是合并。</p></a>
 <a class="card" href="#/guide/rooms"><div class="ci">${ic('chat')}</div><b>话题（房间）</b><p>一件事一个房间，实况文档是它的当前全貌。</p></a>
 <a class="card" href="#/guide/tasks"><div class="ci">${ic('layers')}</div><b>活与看板</b><p>大事拆成几件活同时推进，看板告诉你该谁动。</p></a>
</div>`};

const simple=(title,lede,src,secs,extra='')=>({title,lede,src,updated:'2026-09-24',body:extra+secs.map(([id,h,ps])=>`<h2 id="${id}">${h}<a class="anchor" href="#${id}">#</a></h2>`+ps.map(p=>p.startsWith('<')?p:`<p>${p}</p>`).join('')).join('')});

P['guide/working-with-cheese']=simple('怎么和芝士一起干活','界面上的按钮几分钟就摸熟了，真正需要一点时间的是另一件事：怎么把一件事交给一个 AI 队友。','docs/manual/working-with-cheese.md',[
 ['say-what-not-how','说你要什么，不用说怎么做',['描述你想要的结果和判断标准，而不是一步步的操作。它比你更清楚该先读哪个文件、跑哪条命令；它不知道的是<strong>你心里「做完」长什么样</strong>。']],
 ['the-first-reply','它先回你一句，那是你纠偏的机会',['开工前它会复述它理解的目标和第一步。理解偏了，现在说一声只损失几秒；等它做完再说，损失的是一整轮。']],
 ['interrupt','它干活的时候你可以插话',['不用等它停下来。补一个条件、换一个方向，直接在房间里说，它会在下一步之前读到。']]]);
P['guide/projects']=simple('项目','一个项目就是一处放东西的地方加一个团队：你和 AI 队友做出来的文件都落在这里，队里的人一起看着它。','docs/manual/projects.md',[
 ['what','项目是什么',['项目建在某个团队下，队里的人默认就是它的成员。项目里用话题组织工作，每件事一个房间。']],
 ['upstream','连接你已有的仓库',['想让芝士直接改你已有的 GitHub 仓库，在项目设置里连接上游仓库。连上之后，芝士交付的改动以 PR 的形式进这个仓库，你在验收卡上点「采纳」就是合并。','<div class="callout note">'+ic('info')+'<p>第一次用不需要这一步——项目自带一个放东西的地方。</p></div>']]]);
P['guide/teams']=simple('团队','团队是项目的归属：项目建在哪个团队下，队里的人默认就是这个项目的成员，不用再一个个邀请。','docs/manual/teams.md',[
 ['inside','团队里有什么',['团队的成员、角色和它名下的项目。']],['pending','待处理的邀请在哪',['别人邀请你时，在「团队 → 待定」里看到，点 ✓ 就进来了。']],['personal','一个人用也是一个团队',['「个人」是只有你自己的一个团队，规则完全一样，以后要拉人不用换一种用法。']]]);
P['guide/members']=simple('成员','项目成员来自它所属的团队：团队里的人自动能进团队的所有项目，不需要一个个邀请。','docs/manual/members.md',[
 ['external','外部成员',['团队以外的人可以被邀请成为某个项目的外部成员，只能进这一个项目。']],['mention','点名某个人',['在消息里写 @ 加名字，对方会收到强提醒；只写名字不会通知任何人。']]]);
P['guide/rooms']=simple('话题（房间）','房间是你和芝士谈一件事的地方，界面上它也叫「话题」。','docs/manual/rooms.md',[
 ['one-thing','一件事一个房间',['一个房间对应一段完整的上下文：这件事的来龙去脉、试过什么、结论是什么。<strong>不要把三件不相干的事塞进同一个</strong>——那会让芝士在三条线索之间反复切换，也让你事后翻不出任何一条。','房间是个群聊：人和 AI 队友都是名册上的成员。谁被 @ 到，谁来回答。']],
 ['living-doc','实况文档',['房间右侧那份文档是这个房间的<strong>当前全貌</strong>：目标、已有结论、下一步。聊天记录是过程，实况文档是结果。','它也是可以改的。你直接在上面改一句话，等于给了芝士一条指令。']]]);
P['guide/agents']=simple('AI 队友','芝士是和你一起干活的那个 AI 队友，每个项目可以有好几个，各有各的角色设定和记忆。','docs/manual/agents.md',[
 ['summon','@ 它才会动',['<strong>没有 @ 的消息它收不到。</strong>房间里人和人之间的对话不会惊动它，这是有意的——否则你和同事讨论两句它就插进来了。']],
 ['invite','把队友请进房间',['新房间自带项目的默认队友。要让别的队友也参与，在房间顶部的名册里添加它，和添加一个人是同一个动作。']],
 ['runtime','用什么模型',['项目配置一个主模型；具名的 AI 队友可以从项目的模型目录里单独选一个，不选就用主模型。换模型不改变它的身份和记忆。']]]);
P['guide/tasks']=simple('活与看板','一件事太大的时候，芝士会把它拆成几件「活」，在同一个房间里同时推进。','docs/manual/tasks.md',[
 ['task','活',['活不是另一个房间——它是这个房间派出去的一件具体工作，有自己的简报、进展和结论。拆活是芝士的判断，你要做的是把「要什么」说清楚。']],
 ['board','看板',['<strong>看板答的是「现在该谁动」。</strong>板按状态分列——施工中、交付中、等你——扫一眼就知道有没有什么在等你。']],
 ['conclusion','结论卡',['一件活收尾时会留下一张结论卡。下周有人翻到这条活，看到的是一个结论，而不是一段需要重读的聊天记录。']]]);
P['guide/accept']=simple('验收与采纳','活干完，房间里会出现一张验收卡；你点采纳，这批产出当场并进项目的主线。','docs/manual/accept.md',[
 ['is-merge','采纳就是合并',['验收卡上写着它做了什么、对着你的要求逐条给了什么证据，以及一个「采纳」按钮。','<div class="callout warn">'+ic('warn')+'<p><strong>采纳就是合并本身。</strong>你点下去，那批改动当场进主线——该看的证据要在点之前看完，别指望后面还有一道关。</p></div>','按钮灰着的时候，上方那行字会说清楚是被哪条规则拦住的（检查没过、分支落后 main、PR 上有了新提交）。']]]);
P['guide/compute']=simple('设备与算力','芝士干活要跑在一台机器上，这台机器就是这个房间的运行环境。','docs/manual/compute.md',[
 ['runtime','运行环境',['可以是平台开的云机器，也可以是你自己接入的电脑。登录桌面端就能把这台电脑接成设备，macOS 和 Windows 都支持。']]]);

/* dev */
P['dev/overview']={title:'开发文档概览',lede:'这一栏写给改这个仓库的人：知是是一个 monorepo，Python / FastAPI 后端加 Vue 3 前端，芝士在中央主机上运行、在分配到的机器上执行。',updated:'2026-09-25',src:'README.md',body:`
<div class="callout note">${ic('info')}<p>使用文档写给用平台的人，开发文档写给改代码的人。<strong>两拨读者不混在一页里</strong>——混在一起两边都不好用。</p></div>
<h2 id="run">三分钟跑起来<a class="anchor" href="#run">#</a></h2>
<p>需要 uv、Node.js 22+、pnpm、Docker 和 Taskfile。开发栈只有一套端口：后端 <code>localhost:8081</code>，前端 <code>localhost:3000</code>。</p>
<div class="code"><div class="code-bar"><span class="code-tab on">首次</span><span class="code-tab">日常</span><button class="copy" data-copy aria-label="复制">${ic('copy')}</button></div><pre><span class="c"># 克隆并配置环境</span>
<span class="k">git</span> clone https://github.com/SageSeekerSociety/cheese.git
<span class="k">cd</span> cheese
<span class="k">cp</span> backend/.env.example backend/.env

<span class="c"># 装依赖 + 起基础设施 + 跑迁移</span>
<span class="k">task</span> setup

<span class="c"># 后端和前端一起跑</span>
<span class="k">task</span> dev</pre></div>
<h2 id="architecture">架构一览<a class="anchor" href="#architecture">#</a></h2>
<p>浏览器只和前端、后端打交道；芝士的会话跑在中央主机上，它的文件读写和命令<strong>通过一条常驻服务在分配到的机器上执行</strong>。</p>
<div class="diagram"><div class="dg">
 <div class="box"><b>浏览器 / 桌面端</b><small>Vue 3 + Vuetify</small></div><i class="wire"></i>
 <div class="box"><b>前端 nginx</b><small>SPA · /docs 静态站</small></div><i class="wire"></i>
 <div class="box"><b>后端</b><small>FastAPI · PostgreSQL</small></div><i class="wire"></i>
 <div class="box hl"><b>中央会话主机</b><small>Claude Code / Codex</small></div><i class="wire"></i>
 <div class="stack"><div class="box"><b>自托管设备</b><small>你的电脑</small></div><div class="box"><b>云机器</b><small>MicroCloud</small></div></div>
</div></div>
<h2 id="topics">按主题读<a class="anchor" href="#topics">#</a></h2>
<div class="cards">
 <a class="card" href="#/dev/where-a-turn-runs"><div class="ci">${ic('cpu')}</div><b>一轮活跑在哪台机器上</b><p>今天有几种机器、一轮活怎么被分到其中一台。</p></a>
 <a class="card" href="#/dev/what-the-agent-perceives"><div class="ci">${ic('spark')}</div><b>芝士能感知到什么</b><p>出了岔子，该告诉房间里的人还是干活的芝士。</p></a>
 <a class="card" href="#/dev/api-conventions"><div class="ci">${ic('api')}</div><b>API 约定</b><p>你要发的 URL 不是路由文件里写的那个路径。</p></a>
 <a class="card" href="#/dev/design-system"><div class="ci">${ic('layers')}</div><b>设计系统</b><p>温暖的精密：风格、原则，和从原则推出的规则。</p></a>
 <a class="card" href="#/dev/infrastructure"><div class="ci">${ic('folder')}</div><b>部署、备份与访问</b><p>这个应用跑在哪、怎么发布、数据怎么备份。</p></a>
 <a class="card" href="#/dev/testing-without-docker"><div class="ci">${ic('check')}</div><b>没有 docker 怎么跑测试</b><p>DB 测试要的是一个服务器，不是 docker。</p></a>
</div>`};
const DEVLEDE={
 'workflows':['本地开发','三件套：PostgreSQL（Docker）+ 后端（uvicorn）+ 前端（Vite），端口只有一套。','docs/workflows.md'],
 'testing-without-docker':['没有 docker 怎么跑测试','一台既没有 Postgres 也没有 docker 的容器照样能跑全量测试——DB 测试要的是一个服务器，不是 docker。','docs/testing-without-docker.md'],
 'spec':['产品与实现总纲','知是的核心是三个词：AI、全过程、一同成长。','docs/spec.md'],
 'where-a-turn-runs':['一轮活跑在哪台机器上','芝士干活需要一台机器。这份文档说清楚今天有几种机器、一轮活怎么被分配到其中一台。','docs/where-a-turn-runs.md'],
 'remote-execution':['中央会话与远程执行','Claude Code 跑在中央主机上，它的文件工具和命令通过一条常驻服务在分配到的机器上执行。','docs/remote-execution.md'],
 'what-the-agent-perceives':['芝士能感知到什么','平台上一件事出了岔子，谁应该被告知——房间里的人、正在干活的芝士、还是两个都要？','docs/what-the-agent-perceives.md'],
 'agent-liveness':['一轮什么时候算卡住','一轮活在什么情况下会被平台结束、由谁结束、结束之后谁会知道。','docs/agent-liveness.md'],
 'agent-principles':['平台上的 agent 原则','已经拍板、不再重新讨论的判断。写下来是因为它们此前只存在于对话里。','docs/agent-principles.md'],
 'api-conventions':['API 约定','你要发的 URL 不是你在路由文件里找到的那个路径。','docs/api-conventions.md'],
 'design-system':['设计系统','前端视觉与文案的唯一规范。写页面、改样式、审前端 PR 都以它为准。','docs/design-system.md'],
 'i18n':['中英双语与词条','字符串怎么存、怎么命名、缺翻译怎么办。','docs/i18n.md'],
 'chat-publication':['芝士的发言规则','聊天是协作的时间线：接活、分享发现、交付结果。','docs/chat-publication.md'],
 'infrastructure':['部署、备份与访问','描述这个应用跑在哪、怎么发布、数据怎么备份的唯一一处。','docs/infrastructure.md'],
 'device-self-hosting':['设备自托管','接入的设备为普通房间提供执行环境：项目文件、命令、环境脚本和自定义 MCP 进程。','docs/device-self-hosting.md'],
 'microcloud':['云机器（MicroCloud）','云算力是每个话题一台机器——它从哪来、芝士对它有什么要求、改动怎么发上去。','docs/microcloud.md'],
 'evals':['场景与验收清单','Cheese 2.0 的 Eval 集：每个场景怎么造、怎么验。','docs/evals.md'],
 'ci-feedback':['CI 反馈度量','用 ci-feedback-report 收集一段时间内的 CI 运行，度量反馈有多快。','docs/ci-feedback.md'],
};
for(const[k,[t,l,s]] of Object.entries(DEVLEDE)) P['dev/'+k]={title:t,lede:l,src:s,updated:'2026-09-25',body:`<div class="src-chip">${ic('doc','width:14px;height:14px')}${s}</div>
<div class="callout note">${ic('info')}<p>预览里只放了这份文档的开头。正式上线后，正文直接从仓库里的 <code>${s}</code> 渲染——<strong>开发文档不另写一份</strong>，改了源文件站上就变。</p></div>
<h2 id="summary">它讲什么<a class="anchor" href="#summary">#</a></h2><p>${l}</p>`};

/* api */
P['api/overview']={title:'寻址与认证',lede:'你要发的 URL 不是你在路由文件里找到的那个路径：所有接口挂在 /api 前缀下，用人的 Bearer token 或房间凭据调用。',updated:'2026-09-25',src:'docs/api-conventions.md',body:`
<h2 id="base">基础地址<a class="anchor" href="#base">#</a></h2>
<div class="endpoint"><span class="method">BASE</span>https://okcheese.com/api</div>
<p>路由文件里写的是 <code>/projects/{project_id}/export</code>，发请求时要加上 <code>/api</code> 前缀。</p>
<h2 id="auth">认证<a class="anchor" href="#auth">#</a></h2>
<div class="code"><div class="code-bar"><span class="code-tab on">curl</span><span class="code-tab">Python</span><button class="copy" data-copy aria-label="复制">${ic('copy')}</button></div><pre><span class="k">curl</span> https://okcheese.com/api/projects/<span class="s">$PROJECT_ID</span>/export \\
  -H <span class="s">"Authorization: Bearer $TOKEN"</span> \\
  -o project.zip</pre></div>
<div class="callout warn">${ic('warn')}<p>API 参考这一栏是<strong>建议新增</strong>的：仓库里已经有 API 约定和导出接口的文档，但还没有系统的端点清单。要不要做、做多全，等你定。</p></div>`};
P['api/export']={title:'导出项目',lede:'把一个项目的全部内容打包下载：需要一个对该项目有权限的人的 Bearer token。',src:'docs/project-export.md',updated:'2026-09-20',body:`<div class="endpoint"><span class="method">GET</span>/projects/{project_id}/export</div><h2 id="params">参数<a class="anchor" href="#params">#</a></h2><table><tr><th>名称</th><th>位置</th><th>说明</th></tr><tr><td><code>project_id</code></td><td>路径</td><td>要导出的项目 id</td></tr></table>`};
P['api/accept']={title:'采纳验收卡',lede:'验收卡展示的是一条活的 PR；采纳会调用项目所连代码托管平台的接口把它合并。',src:'docs/accept-is-merge.md',updated:'2026-09-21',body:`<div class="endpoint"><span class="method post">POST</span>/topics/{topic_id}/accept-card</div><h2 id="merge">采纳就是合并<a class="anchor" href="#merge">#</a></h2><p>采纳不是「标记为通过」，而是合并本身。合并被分支保护规则拦下时，卡片会说明是哪一条。</p>`};

/* changelog (real merged PRs, rewritten for people who use the product) */
const CL=[
 ['2026-09-25','周四',[
  ['feat','桌面端会自己更新','有新版本时在后台下好，下次打开就是新的，不用再去官网重新下载。',1695],
  ['feat','每个人一页个人主页','头像、简介和参与的项目集中在一页；「关注」功能下线。',1716],
  ['feat','AI 队友可选 Claude Opus 5.5','在项目的模型目录里就能给队友换上。',1710],
  ['feat','实名信息集中到一页','可以查看、修改，也可以删除。',1711],
  ['imp','房间里不再「跳」','原来内容突然出现或消失的地方，现在有过渡动画，并且可以中途打断。',1681],
  ['imp','「等你处理」只剩一个提示点','发送按钮和未读标记回到琥珀色，正文字号更易读。',1678],
  ['feat','Windows 可用 PowerShell 安装连接器','同时修好了自动更新交接时的一个竞态问题。',1694]]],
 ['2026-09-24','周三',[
  ['feat','登录桌面端即接入这台电脑','macOS 和 Windows 上登录桌面端，这台电脑就成为可用的设备。',1672],
  ['feat','Windows 电脑原生成为设备','不再需要装 WSL。',1682],
  ['feat','新建项目时先问它是做什么的','并用你的回答开出第一个房间。',1327],
  ['imp','验收卡停靠在对话底部','对话的结尾不再被卡片挡住。',1673],
  ['imp','通过团队进入项目','团队以外的人以「外部成员」身份加入单个项目。',1639],
  ['sec','信任设备 30 天','在信任的设备上免于反复验证；敏感操作确认一次后 10 分钟内有效。',1676],
  ['sec','所有账号都需要验证邮箱','并支持用邮件验证码登录和确认身份。',1669]]],
 ['2026-09-23','周二',[
  ['feat','房间里的网页直接在沙箱框中打开','芝士做出的网页在房间里就能看。',1538],
  ['feat','活的改动文件默认折叠','需要时再展开，长列表不再刷屏。',1514],
  ['feat','发布服务条款与隐私政策','注册时记录你的同意。',1509],
  ['sec','解绑第三方账号前需重新验证身份','管理两步验证和通行密钥同样如此。',1540],
  ['fix','登录尝试有次数上限','超长密码也会被正确处理，不再报错。',1497],
  ['fix','子代理读对了 MCP 前缀','修复了工具名前缀取错服务器的问题。',1512]]],
 ['2026-09-22','周一',[
  ['feat','可撤销的项目邀请链接','发一个链接就能邀请人进项目，随时可以作废。',1496],
  ['feat','对话里摆出的文件以标签页打开','房间里的文件变成标签页，代码编辑器按需加载。',1474],
  ['feat','课程：按单元排的时间线','学生点课程链接就进入自己的那个项目。',1457],
  ['fix','房主重连时保留房间状态','断线重连后不再丢失进行中的状态。',1477]]],
];
const TAG={feat:'新功能',imp:'改进',fix:'修复',sec:'安全'};


export { I, ic, NAV, P, CL, TAG };
