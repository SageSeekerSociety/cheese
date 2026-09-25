// The site's shape: which pages exist, in which section and group, in what
// order. Titles, summaries and bodies come from the Markdown in docs/manual/.
// Each user page is served at /docs/<slug>; each developer page at /docs/dev/<slug>.

// [key, tab label, icon, groups: [group label, [slug, ...]]]
export const SECTIONS = [
  ['start', '开始使用', 'rocket', [['入门', ['quickstart', 'working-with-cheese']]]],
  ['tutorials', '教程', 'bulb', [['按身份', ['student-tutorial', 'teacher-tutorial', 'office-tutorial']]]],
  ['features', '功能说明', 'layers', [
    ['协作', ['teams', 'projects', 'rooms', 'agents', 'tasks']],
    ['交付', ['files', 'submissions', 'accept', 'sites']],
    ['教学', ['challenges', 'courses']],
    ['资源', ['devices', 'quota']],
  ]],
  ['faq', '常见问题', 'info', [['排障', ['troubleshooting']]]],
]

// Developer pages live in docs/manual/dev/. Pages whose slug starts with `ref-`
// or `by-` are generated at build time (see gen/), not written by hand.
export const DEV = [
  ['总览', ['overview', 'topology', 'data']],
  ['关键流程', ['turn', 'context', 'llm', 'billing', 'machines', 'cli', 'delivery', 'preview', 'ci', 'docs-site']],
  ['安全与权限', ['auth', 'seats', 'admins']],
  ['参考（自动生成）', ['ref-cli', 'ref-env', 'ref-ci']],
  ['索引（自动生成）', ['by-path', 'by-kind']],
]

// Pages that moved; their old URLs keep working.
export const REDIRECTS = { compute: 'devices', members: 'teams' }

// Hand-picked highlights per release, [text, PR]. The full list comes from git.
export const HIGHLIGHTS = {
  unreleased: {
    feat: [['可以选择 Claude Opus 5.5 作为 AI 队友的模型', 1710], ['实名信息集中到一个页面，能查看、修改和删除', 1711], ['个人主页合为一页，取消「关注」', 1716], ['话题里可以回复某条消息，输入框会带上被回复的内容', 1743], ['重新设计的空间看板上线', 1728]],
    imp: [['聊天栏的消息分组、行距和动效重做', 1738], ['首屏更轻，改用系统字体，打开更快', 1742], ['一轮运行失败时说明发生了什么，房间里可以一键重试', 1727], ['后端发版时，正在运行的芝士会交接给新版本，不再中断', 1733]],
    fix: [['团队工作区滚动不再丢失位置', 1726], ['条款与隐私页可以正常滚动', 1722], ['预览失败时说明是哪一页出错，而不是留白', 1628]],
  },
  '0.18.0': {
    feat: [['课程：按单元排成时间线，测验挂在周上，课程成员与分组', 1457], ['空间：创建和审核题目板，学习标签页能看到学生卡在哪', 1400], ['项目网站：把项目交付里的网页发布成私有网站', 770], ['团队额度：团队配额、项目默认值，话题里可以选用', 731], ['每个 AI 队友有了自己的私聊', 904], ['采纳即合并：验收通过就当场合并', 726], ['项目运行环境可以配置，安装失败能恢复', 734], ['在自己电脑上按目录授权芝士访问文件', 1294], ['修改已有的 Word、PPT、Excel 文件不丢格式', 1146], ['云机器支持挂起和恢复', 1438], ['「帮助与反馈」菜单，反馈可以搜索、点赞和评论', 1441]],
    imp: [['首页直接显示「我手上有什么事」', 1321], ['有事等你处理时会通知你', 1102], ['芝士读网页改走平台受控的通道', 901], ['后端发版不停机', 698], ['手机上空间和团队直接放在页面上，支持安装到桌面', 1152], ['看板自动推导进度，并说明轮到谁', 659]],
    fix: [],
  },
  '0.17.0': {
    feat: [['原有平台与 AI 队友层合并为同一个平台', 46]],
  },
}

export const WHO = {
  学生: [['交一道题目', '在空间里找到老师的题目，和芝士一起做完再提交', 'student-tutorial'], ['让芝士讲清一个概念', '在话题里提问，追问到懂为止', 'working-with-cheese'], ['和同学组队做项目', '建团队、拉同学进项目，一起跟芝士干活', 'teams'], ['额度快用完了', '看自己和团队还剩多少额度', 'quota']],
  '老师 / 助教': [['开一门课', '建课程、按周排单元', 'teacher-tutorial'], ['布置作业和测验', '挂到单元上，设好提交要求', 'courses'], ['收作业、打分', '看提交记录，打分，看学生卡在哪', 'submissions'], ['发布一道题目', '在空间里发题目、管参与者', 'challenges']],
  办公: [['让芝士整理一份周报', '把材料丢进话题，说清要什么格式', 'working-with-cheese'], ['把一件事交给芝士', '描述目标，中途补要求，最后验收', 'office-tutorial'], ['和同事一起推进项目', '团队、项目、话题，谁在做什么一目了然', 'projects'], ['把做好的网页发布出去', '项目里的网页一键发布成网站', 'sites']],
}
