const I = {
  book:'<path d="M4 19.5V5a2 2 0 0 1 2-2h13v16H6.5A2.5 2.5 0 0 0 4 21.5v-2Z"/><path d="M8 7h7"/>',
  code:'<path d="m8 8-5 4 5 4M16 8l5 4-5 4M14 4l-4 16"/>',
  api:'<path d="M4 7h16M4 12h10M4 17h7"/><circle cx="18" cy="16" r="3"/>',
  log:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  arrow:'<path d="M5 12h14M13 6l6 6-6 6"/>',
  rocket:'<path d="M5 15c-1.5 1.3-2 4-2 6 2 0 4.7-.5 6-2M14 4c3-1 6-1 6-1s0 3-1 6l-6 6-5-5 6-6Z"/><circle cx="15" cy="9" r="1.5"/>',
  chat:'<path d="M21 12a8 8 0 0 1-11.8 7L4 20l1.1-4.6A8 8 0 1 1 21 12Z"/>',
  quote:'<path d="M7 7h4v4c0 3-1.5 5-4 6"/><path d="M15 7h4v4c0 3-1.5 5-4 6"/>',
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
  lock:'<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
  tag:'<path d="M3 12V4a1 1 0 0 1 1-1h8l9 9-9 9Z"/><circle cx="8" cy="8" r="1.5"/>',
  spark:'<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M6 18l2.5-2.5M15.5 8.5 18 6"/>'
};
const ic=(n,s)=>`<svg class="ico" viewBox="0 0 24 24"${s?` style="${s}"`:''}>${I[n]}</svg>`;

const TAG={feat:'新功能',imp:'改进',fix:'修复'};

// 一件事怎么走完：[标题, 说明, 示意]
const STEPS=[
 ['描述目标','在话题里说清要什么、给谁、什么时候要。','<div class="bub">帮我把这三份材料整理成一页周报</div>'],
 ['芝士去做','芝士会先说它理解的意思和下一步，然后动手。','<div class="bot">明白，我先读材料，十分钟后给你初稿</div>'],
 ['看进度、补要求','随时插话，它会接着干，不用重来。','<div class="bar"><i></i></div><div class="bub">标题用本周日期</div>'],
 ['验收采纳','检查结果，采纳就合进项目；不满意就退回。','<div><span class="chip">查看改动</span> <span class="chip ok">采纳</span></div>'],
];

export { I, ic, TAG, STEPS };
