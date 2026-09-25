// Builds the docs-site preview: one self-contained HTML.
const fs = require('fs');
const path = require('path');
const { marked } = require('/tmp/node_modules/marked');

const MANUAL = '/home/nictheboy/.cheese/home/de808b13-ffd2-4b8a-9d1d-fba7babe389f/1584a145-a30b-403a-a8f3-cf8d5086af5a/.cheese/tasks/126fa903-1742-48b2-86de-cef592c5da50/docs/manual';
const OUT = process.env.OUT || '/tmp/ds/index.html';

// ---------- logo motion (direction A) lifted from the motion prototype ----------
const tpl = fs.readFileSync('/tmp/cm/template.html', 'utf8');
const parts = JSON.parse(fs.readFileSync('/tmp/cm/parts.json', 'utf8'));
const grads = parts.grads; delete parts.grads;
const cut = (a, b) => { const i = tpl.indexOf(a), j = tpl.indexOf(b, i); if (i < 0 || j < 0) throw new Error('marker ' + a); return tpl.slice(i, j); };
const motionCore = cut('const P = __PARTS__;', '// ---------- 四个方向').replace('__PARTS__', JSON.stringify(parts));
const bubble = cut("{\n  key:'bubble'", "{\n  key:'bite'").trim().replace(/,$/, '');
const stage = cut('function stageFor(svg){', '// 调试用');

// ---------- user manual ----------
const esc = s => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const fix = s => s.replace(/小队/g, '团队').replace(/知是/g, 'Cheese');
function mdPage(file) {
  const raw = fix(fs.readFileSync(path.join(MANUAL, file), 'utf8'));
  const body = raw.replace(/^---\n[\s\S]*?\n---\n/, '');
  const toc = [];
  const renderer = new marked.Renderer();
  renderer.heading = function ({ tokens, depth, text }) {
    let t = this.parser.parseInline(tokens);
    let id = '';
    t = t.replace(/\s*\{#([\w-]+)\}\s*$/, (_, x) => { id = x; return ''; });
    if (!id) id = 'h' + toc.length;
    if (depth === 1) return '';
    if (depth <= 3) toc.push([depth, id, t.replace(/<[^>]+>/g, '')]);
    return `<h${depth} id="${id}">${t}</h${depth}>`;
  };
  renderer.link = function ({ href, tokens }) {
    const t = this.parser.parseInline(tokens);
    const m = /^\/([\w-]+)(?:#([\w-]+))?$/.exec(href);
    if (m) return `<a href="#/docs/${m[1]}${m[2] ? '~' + m[2] : ''}">${t}</a>`;
    return `<a href="${href}" target="_blank" rel="noopener">${t}</a>`;
  };
  renderer.image = ({ text }) => `<span class="imgph">截图：${esc(text || '界面截图')}</span>`;
  const html = marked.parse(body, { renderer });
  const lead = /<p>([\s\S]*?)<\/p>/.exec(html);
  return { html, toc, lead: lead ? lead[1].replace(/<[^>]+>/g, '') : '' };
}

const outline = (lead, points) => ({ draft: true, lead, points });
const USER = [
  { group: '开始使用', pages: [
    ['quickstart', '快速开始', 'quickstart.md'],
    ['working-with-cheese', '与芝士协作', 'working-with-cheese.md'],
  ]},
  { group: '教程', pages: [
    ['tut-student', '学生：在空间里完成第一道题目', outline('从加入空间开始，走完一道题目：看题、参与、让芝士协助、提交。', ['找到老师给的空间和题目', '参与题目，从题目创建自己的项目', '在话题里和芝士一起完成', '按题目要求提交，查看提交记录'])],
    ['tut-teacher', '老师 / 助教：开一门课，收作业、打分', outline('从零开一门课：建课程、排单元、布置作业和测验、看提交并打分。', ['创建课程和课程团队', '按周排单元，挂作业和测验', '邀请学生加入', '查看提交记录、打分，看学生卡在哪里'])],
    ['tut-office', '办公：完成第一个协作项目', outline('一个小团队用 Cheese 完成一件真实的事：从建团队到验收交付。', ['建团队、邀请同事', '建项目、开话题、把任务交给芝士', '中途补充要求、回答芝士的问题', '验收采纳，把成果发布或下载'])],
  ]},
  { group: '使用 Cheese', pages: [
    ['spaces', '空间与题目', outline('空间是老师和组织发布题目的地方。', ['找到空间和题目', '题目详情与参与', '从题目创建项目', '我参与的、我发布的', '发布和编辑题目，参与者管理与数据分析'])],
    ['courses', '课程与作业', outline('课程把教学按单元组织起来。', ['加入课程', '课程首页与单元', '作业与测验', '课程成员与课程团队', '课程设置'])],
    ['teams', '团队', 'teams.md'],
    ['projects', '项目', 'projects.md'],
    ['rooms', '话题', 'rooms.md'],
    ['agents', 'AI 队友', 'agents.md'],
    ['tasks', '任务与看板', 'tasks.md'],
    ['files', '文件与成果', 'files.md'],
    ['submit', '提交', outline('按题目或作业的要求提交成果。', ['提交条件和入口', '表单与文件要求', '发布者如何查看提交'])],
    ['accept', '验收与采纳', 'accept.md'],
    ['sites', '发布网站', outline('把项目里做出的网页发布成一个项目网站。', ['哪些东西能发布', '发布与更新', '谁能打开'])],
    ['devices', '设备与运行环境', 'devices.md'],
    ['quota', '额度与算力', 'quota.md'],
  ]},
  { group: '常见问题', pages: [
    ['troubleshooting', '常见问题与排障', 'troubleshooting.md'],
  ]},
];

const DEV = [
  { group: '架构', pages: [
    ['overview', '系统总览', outline('线上跑着哪些服务、各自管什么，以及它们之间怎么通信。', [
      '后端是一份代码按四种方式启动：主 API、机器连接服务（握着设备和终端的长连接，发版时不重启）、模型隧道（远端机器的模型流量）、代码托管事件中继（接 GitHub 事件）',
      '前端 nginx 同时反代 /api 和 /uploads',
      '模型网关（LiteLLM）与计量代理（mitmproxy）',
      'Forgejo 代码托管、Office 与网页渲染服务',
      'Postgres + Valkey',
    ])],
    ['topology', '部署拓扑', outline('测试环境和正式环境各跑哪些容器，发版时换什么、不换什么。', ['镜像按提交号构建，发版按提交号部署', '随发版替换的应用层 vs 常驻的数据面（机器连接、模型隧道、网关、计量代理）', '后端滚动发版时正在跑的轮怎么交接', '正式环境发版的审批'])],
    ['data', '数据存在哪', outline('每一类数据的位置、谁写、怎么备份。', ['数据库：记录、事件、权限', '上传文件与资料库', '话题工作目录与任务分支', '会话记录', '备份与恢复演练'])],
  ]},
  { group: '关键流程', pages: [
    ['turn', '一条消息怎么变成芝士的一轮', outline('从有人点名 AI 队友，到结果回到房间。', ['点名与投递：平台只投递、不主动发起', '找到或新建这个队友在这个房间的会话', '这一轮要不要机器、租哪台', '骨架（Claude Code / Codex / Pi）启动与续跑', '结果、失败与超时如何回到房间'])],
    ['cli', 'cheese CLI 原理', outline('名字都叫 cheese，其实是两个东西。', [
      '沙盒里的 cheese（单个 Python 文件，一份两用）：会话侧是平台 MCP 工具表，机器上是命令行',
      '命令行只管必须在机器上跑的事：任务目录、同步推送、摆预览、应用预览、取资料、Office 转换与重算',
      '身份靠启动时注入的环境变量（平台地址、令牌、项目、话题）',
      '用户电脑上的 cheese（Go 连接器）：登录、连上之后平台在这台机器上开「屏幕」跑活；它本身不知道屏幕里跑的是什么',
      '哪些动作走 CLI、哪些走 MCP，以及为什么',
    ])],
    ['llm', '模型调用流程', outline('一次模型请求从芝士出发，到拿回 token 的完整路径。', [
      '每个请求先问后端 /llm/admission：能不能跑、走哪条路、用哪个模型名（唯一控制点，换模型不用重启）',
      '订阅路：计量代理把请求转给上游，换上平台的凭证',
      '网关路：改写到 LiteLLM，换上项目自己的虚拟 key',
      '上游密钥从不下发到沙盒和用户机器；远端机器只带自己的短期令牌，由平台换 key 转发',
      '流式、重试与超时',
    ])],
    ['billing', '计费流程', outline('用量怎么计、记在谁头上、用完了怎么办。', [
      '两处计量：网关逐次记账；计量代理逐条写日志，后端定时收进用量表（只收一次）',
      '算力额度：按 token 折算，走网关的按真实花费折算',
      '两道刹车：后端放行前查额度、网关虚拟 key 上的预算上限',
      '额度用完时房间里的平台提示',
      '待拍板：额度单价的两个口径不一致',
    ])],
    ['machines', '设备与机器接入', outline('一台机器怎么变成芝士能用的「手」。', ['连接器登录与自动重连', '自托管设备、云机器、托管机器的区别', '机器上的文件授权', '机器离线时一轮会怎样'])],
    ['delivery', '任务 → 分支 → PR → 验收合并', outline('一条活从开卡到合进主干。', ['开任务：工作目录与分支', '提交同步与 draft PR', '验收卡与闸门', '采纳即合并'])],
    ['preview', '预览与项目网站', outline('房间里的预览和项目网站怎么托管、怎么鉴权。', ['预览子域名与短期凭证换 cookie', '静态文件预览与运行中的应用预览', '发布快照与访问范围'])],
  ]},
  { group: '安全与权限', pages: [
    ['auth', '登录与令牌', outline('浏览器里存了什么、每次请求带什么。', ['访问令牌：浏览器本地存储，请求头携带', '刷新令牌：HttpOnly cookie，只发给登录与刷新接口', '两步验证与受信设备', '芝士与机器使用的令牌'])],
    ['seats', '席位与权限判定', outline('谁能在哪里做什么。', ['席位是唯一的授权载体', '项目、团队、空间的角色', 'AI 队友不能做的管理动作'])],
    ['admins', '平台管理员', outline('后台管理的入口与名单。', ['管理员名单从哪来', '后台各模块', '开发文档的访问控制'])],
  ]},
];

function buildPages(groups, section) {
  const out = [];
  for (const g of groups) for (const [slug, title, src] of g.pages) {
    const p = { slug, title, group: g.group, section };
    if (typeof src === 'string') Object.assign(p, mdPage(src));
    else {
      p.draft = true; p.lead = src.lead;
      p.html = `<p class="lead">${esc(src.lead)}</p><div class="draft-box"><div class="draft-tag">提纲 · 待写</div><ul>${src.points.map(x => `<li>${esc(x)}</li>`).join('')}</ul></div>`;
      p.toc = [];
    }
    out.push(p);
  }
  return out;
}
const pages = [...buildPages(USER, 'docs'), ...buildPages(DEV, 'dev')];
const nav = { docs: USER.map(g => ({ group: g.group, slugs: g.pages.map(p => p[0]) })), dev: DEV.map(g => ({ group: g.group, slugs: g.pages.map(p => p[0]) })) };

// FAQ from the troubleshooting page
const trouble = fix(fs.readFileSync(path.join(MANUAL, 'troubleshooting.md'), 'utf8'));
const faq = [...trouble.matchAll(/^## (.+?)\s*\{#([\w-]+)\}\n+([\s\S]*?)(?=\n## |$)/gm)].map(m => ({ q: m[1], id: m[2], a: marked.parseInline(m[3].trim().split(/\n\n/)[0]) }));

// ---------- changelog ----------
const tsv = f => fs.readFileSync(f, 'utf8').trim().split('\n').map(l => l.split('\t')).map(([h, d, s]) => {
  const pr = (/\(#(\d+)\)\s*$/.exec(s) || [])[1];
  const kind = (/^(\w+)/.exec(s) || [])[1];
  return { d, s: s.replace(/\s*\(#\d+\)\s*$/, ''), pr, kind };
}).filter(x => x.pr);
const unrel = tsv('/tmp/ds/unrel.tsv'), r018 = tsv('/tmp/ds/r018.tsv');
const count = list => ({ feat: list.filter(x => x.kind === 'feat').length, fix: list.filter(x => x.kind === 'fix').length, all: list.length });
const RELEASES = [
  { ver: '未发布', tag: 'unreleased', date: '9 月 23 日之后', env: '已在测试环境，下次正式发布时带上', stats: count(unrel), list: unrel,
    groups: [
      ['新功能', [['可以选择 Claude Opus 5.5 作为 AI 队友的模型', 1710], ['实名信息集中到一个页面，能查看、修改和删除', 1711], ['个人主页合为一页，取消「关注」', 1716], ['话题里可以回复某条消息，输入框会带上被回复的内容', 1743], ['重新设计的空间看板上线', 1728]]],
      ['改进', [['聊天栏的消息分组、行距和动效重做', 1738], ['首屏更轻，改用系统字体，打开更快', 1742], ['一轮运行失败时说明发生了什么，房间里可以一键重试', 1727], ['后端发版时，正在运行的芝士会交接给新版本，不再中断', 1733]]],
      ['修复', [['团队工作区滚动不再丢失位置', 1726], ['条款与隐私页可以正常滚动', 1722], ['预览失败时说明是哪一页出错，而不是留白', 1628]]],
    ] },
  { ver: '0.18.0', tag: 'proposed', date: '2026-09-23', env: '上线正式环境 · 建议版本号', stats: count(r018), list: r018,
    groups: [
      ['新功能', [['课程：按单元排成时间线，测验挂在周上，课程成员与分组', 1457], ['空间：创建和审核题目板，学习标签页能看到学生卡在哪', 1400], ['项目网站：把项目交付里的网页发布成私有网站', 770], ['团队额度：团队配额、项目默认值，话题里可以选用', 731], ['每个 AI 队友有了自己的私聊', 904], ['采纳即合并：验收通过就当场合并', 726], ['项目运行环境可以配置，安装失败能恢复', 734], ['在自己电脑上按目录授权芝士访问文件', 1294], ['修改已有的 Word、PPT、Excel 文件不丢格式', 1146], ['云机器支持挂起和恢复', 1438], ['「帮助与反馈」菜单，反馈可以搜索、点赞和评论', 1441]]],
      ['改进', [['首页直接显示「我手上有什么事」', 1321], ['有事等你处理时会通知你', 1102], ['芝士读网页改走平台受控的通道', 901], ['后端发版不停机', 698], ['手机上空间和团队直接放在页面上，支持安装到桌面', 1152], ['看板自动推导进度，并说明轮到谁', 659]]],
      ['修复', [[`共 ${count(r018).fix} 项修复，集中在远端执行、预览、会话恢复和文案`, null]]],
    ] },
  { ver: '0.17.0', tag: 'release', date: '2026-07-15', env: '正式发布', stats: { all: 1, feat: 1, fix: 0 }, list: [{ d: '2026-07-15', s: 'Fusion merge: unify the platform and the AI layer into one platform', pr: '46', kind: 'feat' }],
    groups: [['新功能', [['原有平台与 AI 队友层合并为同一个平台', 46]]]] },
];

const DATA = { pages, nav, faq, releases: RELEASES };
let html = fs.readFileSync('/tmp/ds/shell.html', 'utf8');
html = html.replace('/*__GRADS__*/', grads)
  .replace('/*__DATA__*/', 'const DATA = ' + JSON.stringify(DATA).replace(/<\/script/g, '<\\/script') + ';')
  .replace('/*__MOTION__*/', motionCore + '\nconst BUBBLE = ' + bubble + ';\n' + stage);
fs.mkdirSync(path.dirname(OUT), { recursive: true });
fs.writeFileSync(OUT, html);
console.log(OUT, html.length, 'pages', pages.length, 'faq', faq.length, 'unrel', unrel.length, 'r018', r018.length);
