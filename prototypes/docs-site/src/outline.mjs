// Site structure and hand-written copy. A string source is a docs/manual file;
// an outline() is a page that is not written yet.
const outline = (lede, points) => ({ lede, points })

// User docs are split into sibling sections so each can grow on its own.
// [key, tab label, icon, groups]
export const SECTIONS = [
  ['start', '开始使用', 'rocket', [
    ['入门', [
      ['quickstart', '快速开始', 'quickstart.md'],
      ['working-with-cheese', '与芝士协作', 'working-with-cheese.md'],
    ]],
  ]],
  ['tutorials', '教程', 'bulb', [
    ['按身份', [
      ['tut-student', '学生：在空间里完成第一道题目', 'local:tut-student.md'],
      ['tut-teacher', '老师 / 助教：开一门课，收作业、打分', 'local:tut-teacher.md'],
      ['tut-office', '办公：完成第一个协作项目', 'local:tut-office.md'],
    ]],
  ]],
  ['features', '功能说明', 'layers', [
    ['协作', [
      ['teams', '团队', 'teams.md'],
      ['projects', '项目', 'projects.md'],
      ['rooms', '话题', 'rooms.md'],
      ['agents', 'AI 队友', 'agents.md'],
      ['tasks', '任务与看板', 'tasks.md'],
    ]],
    ['交付', [
      ['files', '文件与成果', 'files.md'],
      ['submit', '提交', 'local:submit.md'],
      ['accept', '验收与采纳', 'accept.md'],
      ['sites', '发布网站', 'local:sites.md'],
    ]],
    ['教学', [
      ['spaces', '空间与题目', 'local:spaces.md'],
      ['courses', '课程与作业', 'local:courses.md'],
    ]],
    ['资源', [
      ['devices', '设备与运行环境', 'devices.md'],
      ['quota', '额度与算力', 'quota.md'],
    ]],
  ]],
  ['faq', '常见问题', 'info', [
    ['排障', [
      ['troubleshooting', '常见问题与排障', 'troubleshooting.md'],
    ]],
  ]],
]

export const DEV = [
  ['架构', [
    ['overview', '系统总览', 'local:dev/overview.md'],
    ['topology', '部署拓扑', 'local:dev/topology.md'],
    ['data', '数据存在哪', 'local:dev/data.md'],
  ]],
  ['关键流程', [
    ['turn', '一条消息怎么变成芝士的一轮', 'local:dev/turn.md'],
    ['cli', 'cheese CLI 原理', 'local:dev/cli.md'],
    ['llm', '模型调用流程', 'local:dev/llm.md'],
    ['billing', '计费流程', 'local:dev/billing.md'],
    ['machines', '设备与机器接入', 'local:dev/machines.md'],
    ['delivery', '任务 → 分支 → PR → 验收合并', 'local:dev/delivery.md'],
    ['preview', '预览与项目网站', 'local:dev/preview.md'],
  ]],
  ['安全与权限', [
    ['auth', '登录与令牌', 'local:dev/auth.md'],
    ['seats', '席位与权限判定', 'local:dev/seats.md'],
    ['admins', '平台管理员', 'local:dev/admins.md'],
  ]],
]

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
  学生: [['交一道题目', '在空间里找到老师的题目，和芝士一起做完再提交', 'tut-student'], ['让芝士讲清一个概念', '在话题里提问，追问到懂为止', 'working-with-cheese'], ['和同学组队做项目', '建团队、拉同学进项目，一起跟芝士干活', 'teams'], ['额度快用完了', '看自己和团队还剩多少额度', 'quota']],
  '老师 / 助教': [['开一门课', '建课程、按周排单元', 'tut-teacher'], ['布置作业和测验', '挂到单元上，设好提交要求', 'courses'], ['收作业、打分', '看提交记录，打分，看学生卡在哪', 'submit'], ['发布一道题目', '在空间里发题目、管参与者', 'spaces']],
  办公: [['让芝士整理一份周报', '把材料丢进话题，说清要什么格式', 'working-with-cheese'], ['把一件事交给芝士', '描述目标，中途补要求，最后验收', 'tut-office'], ['和同事一起推进项目', '团队、项目、话题，谁在做什么一目了然', 'projects'], ['把做好的网页发布出去', '项目里的网页一键发布成网站', 'sites']],
}
