// 芝士Opus 第二版：按明确阵容采购（规程·序列Ⅰ终极，四人）。
// 思路：每个座位在 R2~R5 选定一套阵容（主盟约 + 叠层引擎 + 带队输出 + 坦），之后只为这套阵容买卡、合精锐、刷新；
// 层数全部压在主盟约上。上场和站位沿用模拟器机器人的排练逻辑，但上场估值加上本阵容权重和拉表实测强度。
// 只读自己的持有、可见商店与公开库存，不读隐藏信息。
import { createBotPrepSteps, botPickBand, setUnitBonus, sellJunk as legacySellJunk } from './bot.js';
import { OPUS_CARRY } from './botOpusCarry.js';

// 阵容：卡名 → 权重（10 = 带队核心，7~9 = 叠层引擎/强输出，3~5 = 凑盟约/前排）。tank 标记前排。
const env = (k, d) => (process.env[k] != null ? +process.env[k] : d);
export const COMPS = {
  // 叙拉古：6 人激活强化（异常状态 + 真实伤害），模拟里 300 层就能清 R13
  sira: { main: 'siracusaShip', name: '叙拉古', cards: { 拉普兰德: 6, 复奏: 8, 奥斯塔: 5, 忍冬: 5, 伺夜: 8, 斥罪: 10, 安洁莉娜: 7, 贝洛内: 9, 铃兰: 6, 荒芜拉普兰德: 10, 摆渡人: 3 } },
  // 乌萨斯：6 人强化（生命流失 + 阻挡法伤），500 层清 R13，赫拉格打领袖
  ursus: { main: 'ursusShip', name: '乌萨斯', cards: { 折桠: 6, 烈夏: 5, 真理: 4, 奥尔佳: 8, 乌啾: 6, 维伊: 9, 赫拉格: 10, 冻河: 9, 怒潮凛冬: 8, 塔露拉: 9, 摆渡人: 3 } },
  // 卡西米尔：150 层就能稳 R11，玛恩纳打领袖强
  kazi: { main: 'kazimierzShip', name: '卡西米尔', cards: { 野鬃: 6, 砾: 8, 历阵锐枪芬: 5, 瑕光: 9, 远牙: 7, 玛恩纳: 10, 焰尾: 8, 薇薇安娜: 7, 耀骑士临光: 9, 锏: 7, 摆渡人: 3 } },
  // 精准：清怪一般，打领袖最强（雪猎、承曦格雷伊）
  preci: { main: 'preciShip', name: '精准', cards: { 灰喉: 5, 霜叶: 2, 雪猎: 10, 承曦格雷伊: 9, 空弦: 6, 旅骨: 4, 伺夜: 6, 黑键: 7, 霍尔海雅: 7, 远牙: 5, 迷迭香: 9, 鸿雪: 8, 蕾缪安: 7, 帕拉斯: 7, 摆渡人: 4 } },
  yan: { main: 'yanShip', name: '炎', cards: { 拜松: 5, 矩: 6, 乌有: 5, 风絮: 8, 仇白: 10, 余: 9, 黍: 9, 老鲤: 8, 夕: 10, 烛煌: 8, 华法琳: 4, 摆渡人: 4 } },
  sarg: { main: 'sargonShip', name: '萨尔贡', cards: { 艾丝黛尔: 6, 泡泡: 6, 莎草: 5, 至简: 3, 卡涅利安: 7, 森蚺: 9, 异客: 8, 鸿雪: 8, 佩佩: 8, 真言: 9, 摆渡人: 3 } },
  leth: { main: 'lethaniaShip', name: '莱塔尼亚', cards: { 地灵: env('OPUS2_L1', 4), 卡达: 3, 折光: env('OPUS2_L2', 6), 复奏: 8, 车尔尼: env('OPUS2_L3', 5), 艾雅法拉: 10, 黑键: 6, 卡涅利安: 7, 'Miss.Christine': 4, 霍尔海雅: 5, 逻各斯: 8, 纯烬艾雅法拉: 9, 薇薇安娜: 7, 锏: 6, 摆渡人: 3 } },
};
// 任何阵容都要的通用卡：魔王（核心盟约 +20）、两张整备区也生效的助力引擎
const UNIVERSAL = { 魔王: 7, 夏栎: env('OPUS2_XL', 6), 星源: env('OPUS2_XY', 7) };
// 这里只记录会在休整期或战斗中持续加层的卡；普通卡的数值，精锐约翻倍。
// “获得时”卡只在获得/合成时触发，不能当作每回合叠层；“本回合每获得一名干员”卡另列。
export const STACKERS = {
  sira: { 复奏: 4, 伺夜: 4, 荒芜拉普兰德: 10 },
  preci: { 迷迭香: 10, 黑键: 4, 鸿雪: 8, 空弦: 3, 霍尔海雅: 4 },
  ursus: { 烈夏: 4, 折桠: 2, 乌啾: 3, 赫拉格: 4, 冻河: 8, 塔露拉: 6 },
  kazi: { 野鬃: 2, 砾: 6, 瑕光: 3, 耀骑士临光: 6, 焰尾: 5 },
  yan: { 矩: 6, 余: 4, 烛煌: 6 },
  sarg: { 艾丝黛尔: 2, 泡泡: 4, 森蚺: 4, 鸿雪: 8, 佩佩: 4 },
  leth: { 地灵: 2, 折光: 8, 复奏: 4, 黑键: 4, 纯烬艾雅法拉: 10 },
};
export const ON_GAIN = {
  // SERVER_GAIN 的每次获得值；不要把这些卡当作常驻每回合层数。
  preci: { 灰喉: 2, 雪猎: 5 },
  yan: { 拜松: 2, 仇白: 6, 黍: 8 },
};
// 这些卡是叠层引擎，但触发条件不是“每回合自动加层”：
// 拉普兰德/安洁莉娜看刷新，风絮/夕看本回合获得干员数。
const REFRESH_ENGINES = { sira: { 拉普兰德: 4, 安洁莉娜: 4 } };
const GAIN_ENGINES = { yan: { 风絮: 2, 夕: 4 } };
const UNI_STACK = { 夏栎: 2, 星源: 4, 魔王: 10 };
const stackOf = (ps, n) => (STACKERS[st(ps).comp]?.[n]) || UNI_STACK[n] || 0;
const stackTargetOf = (ps, n) => stackOf(ps, n) || ON_GAIN[st(ps).comp]?.[n] || REFRESH_ENGINES[st(ps).comp]?.[n] || GAIN_ENGINES[st(ps).comp]?.[n] || 0;
const BENCH_ENGINE = new Set(['夏栎', '星源']);   // 整备区也生效：放整备区，给层数最多的已激活盟约（主盟约）加层
const SEAT_PREF = (process.env.OPUS2_PREF || 'mix') === 'mix'
  ? [['sarg', 'leth', 'ursus'], ['leth', 'sarg', 'ursus'], ['sarg', 'leth', 'ursus'], ['leth', 'ursus', 'sarg']]
  : process.env.OPUS2_PREF === 'sarg4'
  ? [['sarg', 'leth', 'ursus'], ['sarg', 'leth', 'ursus'], ['sarg', 'ursus', 'leth'], ['sarg', 'leth', 'sira']]
  : process.env.OPUS2_PREF === 'sarg2'
  ? [['sarg', 'leth', 'ursus'], ['sarg', 'leth', 'ursus'], ['leth', 'ursus', 'sira'], ['ursus', 'leth', 'sira']]
  : process.env.OPUS2_PREF === 'sarg'
  ? [['sarg', 'leth', 'ursus'], ['leth', 'sarg', 'sira'], ['ursus', 'sira', 'leth'], ['sira', 'ursus', 'sarg']]
  : process.env.OPUS2_PREF === 'leth'
  ? [['leth', 'sira', 'kazi'], ['leth', 'ursus', 'sira'], ['leth', 'kazi', 'ursus'], ['leth', 'sira', 'preci']]
  : [['kazi', 'sira', 'ursus'], ['sira', 'ursus', 'kazi'], ['ursus', 'kazi', 'sira'], ['preci', 'leth', 'kazi']];
const LEVEL = (process.env.OPUS_LEVEL || '5,7,9,11').split(',').map(Number);
const LOCK = env('OPUS2_LOCK', 5);

const nameOf = (gd, id) => gd.chess(id)?.name;
const st = (ps) => ((ps.botStrategyState ??= {}).opus2 ??= { comp: null });
function holdings(gd, ps) {
  const normal = new Map(), elite = new Set();
  for (const p of ps.allChess()) { const n = nameOf(gd, p.id); if (!n) continue; if (gd.isGolden(p.id)) elite.add(n); else normal.set(n, (normal.get(n) || 0) + 1); }
  return { normal, elite };
}
const disabledBond = (m, gd, b) => gd.modeInactiveBonds.has(b) || (m.disabledBonds instanceof Set ? m.disabledBonds.has(b) : (m.disabledBonds || []).includes(b));
const NAME_INDEX = new WeakMap();
function nameIndex(gd) {
  if (NAME_INDEX.has(gd)) return NAME_INDEX.get(gd);
  const map = new Map(); for (const id of gd.visibleChess) { const r = gd.chess(id); if (r && !r.isGolden && r.name && !map.has(r.name)) map.set(r.name, gd.baseIdOf(id)); }
  NAME_INDEX.set(gd, map); return map;
}
function cardLive(m, gd, name) {
  const base = nameIndex(gd).get(name); if (!base) return false; const r = gd.chess(base);
  return !!r && !(m.chessExcluded ? m.chessExcluded(r) : false);
}
function visibleCompHits(m, ps, c) {
  const gd = ps.gd || m.gd;
  let hits = 0;
  for (const slot of ps.shop?.slots || []) {
    if (!slot || slot.sold || slot.kind !== 'chess') continue;
    const n = nameOf(gd, slot.id);
    if (n && c.cards[n]) hits += c.cards[n] >= 7 ? 3 : 1;
  }
  return hits;
}
function compScore(m, ps, key, h) {
  const gd = ps.gd || m.gd; const c = COMPS[key];
  if (disabledBond(m, gd, c.main)) return -Infinity;
  const live = Object.entries(c.cards).filter(([n]) => cardLive(m, gd, n));
  if (live.length < 7) return -Infinity;
  let s = live.reduce((a, [, w]) => a + w, 0) * 0.3;
  for (const [n, w] of live) s += h.elite.has(n) ? w * 3 : (h.normal.get(n) || 0) * w;
  // 只读当前可见商店：R2~R5 先跟发牌走，不能只按座位固定偏好锁盟约。
  s += visibleCompHits(m, ps, c) * env('OPUS2_VISIBLEW', 8);
  const pref = SEAT_PREF[ps.seat % 4] || []; const i = pref.indexOf(key); if (i >= 0) s += (3 - i) * env('OPUS2_PREFW', 10);
  for (const q of m.players?.values?.() || []) if (q !== ps && q.alive && q.botStrategyState?.opus2?.comp === key) s -= (process.env.OPUS2_SHARE ?? 'sarg,leth').split(',').includes(key) ? 0 : env('OPUS2_CLASH', 40);
  return s;
}
function chooseComp(m, ps) {
  const S = st(ps); const gd = ps.gd || m.gd; const h = holdings(gd, ps);
  if (S.comp && m.round >= LOCK && compScore(m, ps, S.comp, h) > -Infinity) return S.comp;
  let best = S.comp, bs = -Infinity;
  for (const k of Object.keys(COMPS)) { const s = compScore(m, ps, k, h) + (k === S.comp ? 6 : 0); if (s > bs) { bs = s; best = k; } }
  S.comp = best; return best;
}
function weightOf(ps, name) {
  const c = COMPS[st(ps).comp]; return (c && c.cards[name]) || UNIVERSAL[name] || 0;
}

// 主C：R5 起四个座位里阵容最强的那个当主C（有粘性），其他座位不跟它抢本阵容的卡，让它更容易零漏怪去联防
function carrySeat(m) {
  if (!env('OPUS2_CARRY', 0) || m.round < 5) return null;
  const S = (m._opus2Carry ??= { pid: null, round: 0 });
  if (S.round === m.round) return S.pid; S.round = m.round;
  let best = null, bs = -Infinity;
  for (const q of m.players?.values?.() || []) {
    if (!q.alive || !q.botStrategyState?.opus2) continue; const gd = q.gd || m.gd;
    let v = 0; for (const p of q.board.values()) if (p && p.kind === 'chess') v += (gd.tierOf(p.id) || 1) * (gd.isGolden(p.id) ? 3 : 1);
    v += q.lp * 0.3 + (q.playerId === S.pid ? 8 : 0);
    if (v > bs) { bs = v; best = q.playerId; }
  }
  S.pid = best; return best;
}
function yieldsToCarry(m, ps, name) {
  const c = carrySeat(m); if (!c || c === ps.playerId) return false;
  const q = m.players.get ? m.players.get(c) : null; const comp = q?.botStrategyState?.opus2?.comp;
  return !!(comp && COMPS[comp]?.cards[name] >= 5);
}
// 领袖战专用卡：雪猎、承曦格雷伊是 2 阶卡、打领袖最猛（拉表 60 秒 136 万 / 81 万），R3 起每个座位囤一种，凑精锐留到 R14
const BOSS_CARDS = ['雪猎', '承曦格雷伊'];
const bossCard = (ps) => (env('OPUS2_BOSSCOL', 0) ? BOSS_CARDS[ps.seat % 2] : null);
function eliteCount(h) { return h.elite.size; }
function targetNames(h, ps) {
  const comp = COMPS[st(ps).comp];
  const allowed = new Set([...Object.keys(comp?.cards || {}), ...Object.keys(UNIVERSAL)]);
  const out = new Set([...h.elite].filter((n) => allowed.has(n)));
  for (const [n, count] of h.normal) if (count > 0 && allowed.has(n)) out.add(n);
  return out;
}
function isMainMember(gd, name, comp) {
  return !!(comp && (gd.chess(nameIndex(gd).get(name))?.bonds || []).includes(comp.main));
}
function mayOpenTarget(m, ps, name, h, tier) {
  const comp = COMPS[st(ps).comp];
  if (!comp) return false;
  // 主盟约凑六人是硬约束，不能被「目标卡不超过五种」误拦。
  if (isMainMember(ps.gd || m.gd, name, comp) && mainMembers(ps.gd || m.gd, ps, comp.main) < 6) return true;
  if (tier >= 5 && m.round < 8) return false;
  const targets = targetNames(h, ps);
  const limit = m.round <= 6 ? 5 : 6;
  if (targets.size >= limit) return false;
  // 新开的非核心卡必须是叠层卡；否则它只能在已有对子时补。
  return stackTargetOf(ps, name) > 0 && tier <= 4;
}
// 买卡评分：合成 > 本阵容卡（已有张数越多越高）> 空位填充；本阵容 5~6 阶卡单张也要
function buyScore(m, ps, slot, h, room) {
  const gd = ps.gd || m.gd; const n = nameOf(gd, slot.id); if (!n) return -1;
  const golden = gd.isGolden(slot.id); const tier = gd.tierOf(slot.id) || 1;
  const w = weightOf(ps, n); const have = h.normal.get(n) || 0;
  if (h.elite.has(n) && !golden) return -1;
  if (!have && yieldsToCarry(m, ps, n)) return -1;
  if (n === bossCard(ps) && m.round >= 3) return 45 + have * 25;
  const need = gd.mergeCount(slot.id) || 3;
  const carry = OPUS_CARRY[n] ? OPUS_CARRY[n].late * 6 + OPUS_CARRY[n].mid * 4 : 0;
  // 最终攻势（R14，R13 起囤卡）：按拉表的领袖伤害买，不管在不在阵容里
  if (env('OPUS2_FA', 1) && m.round >= m.gd.bossRound - 1 && OPUS_CARRY[n]?.boss >= 0.05) {
    const fa = OPUS_CARRY[n].boss * 100 * (have + 1 >= need ? 2 : 1);
    if (m.round >= m.gd.bossRound || room > 0 || freeHand(ps) > 1) return 30 + fa + w * 2;
  }
  const sk = env('OPUS2_STK', 0) * stackOf(ps, n) * (m.round <= 7 ? 2 : 1);
  // 精锐是已经完成合成的高价值目标，不能被“新目标种类”限制误拦。
  if (golden) return 60 + w * 5 + carry + sk;
  if (!have && w && !mayOpenTarget(m, ps, n, h, tier)) return -1;
  if (have + 1 >= need) return (w ? 70 + w * 5 : 30 + tier * 3) + carry;
  // 凑满 6 名不同的主盟约成员：实测有 6 人核心强化的座位零漏怪率高得多（萨尔贡 61%、莱塔尼亚 29%，没有的 14%）
  const comp = COMPS[st(ps).comp];
  const mem = comp && !have && (gd.chess(slot.id)?.bonds || []).includes(comp.main) ? env('OPUS2_MEM', 30) * Math.max(0, 6 - mainMembers(gd, ps, comp.main)) / 3 : 0;
  if (w) return 25 + w * 4 + have * 15 + carry * 0.5 + sk + mem;
  if (room > 0) return 6 + tier * 2 + carry;   // 阵容没凑齐时先用别的卡占位
  return -1;
}
function mainMembers(gd, ps, bond) {
  const seen = new Set(); for (const p of ps.allChess()) { const r = gd.chess(p.id); if (r && (r.bonds || []).includes(bond)) seen.add(gd.baseIdOf(p.id)); }
  return seen.size;
}
function deployed(ps) { return [...ps.board.values()].filter((p) => p && p.kind === 'chess').length; }
function freeHand(ps) { return ps.hand.filter((x) => !x).length; }
// 整备区满时卖掉最没用的一张（非本阵容、非对子、阶数低）
function sellWorst(m, ps, keepName) {
  const gd = ps.gd || m.gd; const h = holdings(gd, ps);
  const cands = ps.hand.filter((p) => p && p.kind === 'chess').map((p) => {
    const n = nameOf(gd, p.id); const w = weightOf(ps, n); const g = gd.isGolden(p.id);
    return { p, v: (g ? 40 : 0) + w * 6 + (h.normal.get(n) || 0) * (w ? 6 : 2) + (gd.tierOf(p.id) || 1) };
  }).filter((x) => nameOf(gd, x.p.id) !== keepName && nameOf(gd, x.p.id) !== bossCard(ps)).sort((a, b) => a.v - b.v);
  if (!cands.length) return false;
  return !!ps.sell(cands[0].p.uid)?.ok;
}
function wantsLevel(m, ps) {
  const gd = ps.gd || m.gd;
  if (ps.shop.level >= gd.maxShopLevel) return false;
  // 座位错峰：四个座位用不同的升本节奏，让不同回合各有一个人处在强势期（联防只要有一人零漏怪）
  const per = (process.env.OPUS2_SEATLV ?? '5,7,8,11|5,7,9,11').split('|').filter(Boolean);
  const sched = per.length ? per[ps.seat % per.length].split(',').map(Number) : LEVEL;
  const due = sched[ps.shop.level + 1 - 2] ?? 99;
  if (m.round < due || ps.funds < Math.max(0, ps.shop.upgradePrice)) return false;
  const next = ps.shop.level + 1;
  if (next === 5) {
    const h = holdings(gd, ps);
    const comp = COMPS[st(ps).comp];
    const core = comp ? mainMembers(gd, ps, comp.main) : 0;
    // 5 本只给高血量、已有六人主盟约且已经形成精锐的席位；
    // 中低血量提前升本会同时损失刷新次数和低阶精锐。
    const lp = Number.isFinite(ps.lp) ? ps.lp : 0;
    if (m.round < m.gd.bossRound - 2 && (lp < 18 || core < 6 || eliteCount(h) < 4)) return false;
  }
  return true;
}
// 还有没有值得刷的目标：本阵容里阶数够得着、还没合精锐的卡
function hasTargets(m, ps) {
  const gd = ps.gd || m.gd; const c = COMPS[st(ps).comp]; if (!c) return false; const h = holdings(gd, ps);
  return Object.keys(c.cards).some((n) => { const b = nameIndex(gd).get(n); return b && cardLive(m, gd, n) && !h.elite.has(n) && (gd.tierOf(b) || 9) <= ps.shop.level; });
}
function buyPass(m, ps) {
  const gd = ps.gd || m.gd;
  for (let guard = 0; guard < 20; guard++) {
    const h = holdings(gd, ps);
    const room = Math.max(0, ps.deployCap - deployed(ps) - Math.max(0, ps.allChess().length - deployed(ps) - 0)) ;
    let best = null;
    ps.shop.slots.forEach((s, i) => {
      if (!s || s.sold || s.kind !== 'chess') return; const price = ps.priceOf(s); if (price > ps.funds) return;
      const sc = buyScore(m, ps, s, h, Math.max(0, ps.deployCap - ps.allChess().length));
      if (sc > 0 && (!best || sc > best.sc)) best = { i, sc, s };
    });
    if (!best) return;
    const n = nameOf(gd, best.s.id); const merges = (h.normal.get(n) || 0) + 1 >= (gd.mergeCount(best.s.id) || 3);
    if (freeHand(ps) <= 0 && !merges) { if (best.sc < 25 || !sellWorst(m, ps, n)) return; }
    if (freeHand(ps) <= 0 && merges) { if (!sellWorst(m, ps, n)) return; }
    if (!ps.buy(best.i)?.ok) return;
  }
}
function* buyLoopSteps(m, ps, { fillOnly = false } = {}) {
  const gd = ps.gd || m.gd; chooseComp(m, ps);
  buyPass(m, ps); yield;
  if (fillOnly) return;
  if (wantsLevel(m, ps)) ps.levelUp();
  buyPass(m, ps);
  // 余钱全刷：资金每回合清零（坎诺特除外），没花掉就浪费
  for (let r = 0; r < 30; r++) {
    const free = ps.shop.freeRefreshes > 0;
    if (!free && ps.funds < gd.refreshPrice) break;
    if (!hasTargets(m, ps) && !free && deployed(ps) >= ps.deployCap && m.round < m.gd.bossRound) break;
    if (!ps.refresh()?.ok) break;
    buyPass(m, ps);
    if (r % 4 === 3) yield;
  }
}
function levelUp(m, ps) { if (wantsLevel(m, ps)) ps.levelUp(); }
function sellJunk(m, ps) {
  const gd = ps.gd || m.gd; const h = holdings(gd, ps);
  // 已合精锐的卡多出来的单张卖掉
  for (const p of ps.allChess()) { const n = nameOf(gd, p.id); if (!gd.isGolden(p.id) && h.elite.has(n)) ps.sell(p.uid); }
  // R4 起整备区里不属于本阵容的单卡卖掉（对子留到 R6），腾位置也回点钱
  if (m.round >= env('OPUS2_CLEAN', 4)) for (const p of ps.hand) {
    if (!p || p.kind !== 'chess' || gd.isGolden(p.id)) continue; const n = nameOf(gd, p.id);
    if (weightOf(ps, n)) continue; if ((h.normal.get(n) || 0) >= 2 && m.round < 6) continue;
    if (m.round >= m.gd.bossRound - 1 && OPUS_CARRY[n]?.boss >= 0.05) continue;
    if (n === bossCard(ps)) continue;
    ps.sell(p.uid);
  }
}
function maybeFreeze(m, ps) {
  const gd = ps.gd || m.gd; const h = holdings(gd, ps);
  const want = ps.shop.slots.some((s) => s && !s.sold && s.kind === 'chess' && weightOf(ps, nameOf(gd, s.id)) >= 4 && !h.elite.has(nameOf(gd, s.id)));
  if (want !== !!ps.shop.frozen) ps.freeze();
}
// 风絮/夕/可露希尔在场：1 块钱的真理、至简买了就卖；吉星 R9 前买来拿免费招募再卖
const COUNTERS = new Set(['风絮', '夕', '可露希尔']); const CHEAP = new Set(['真理', '至简']);
function tricks(m, ps) {
  const gd = ps.gd || m.gd;
  const counter = [...ps.board.values()].some((p) => p && p.kind === 'chess' && COUNTERS.has(nameOf(gd, p.id)));
  for (let g = 0; g < 8; g++) {
    let acted = false;
    ps.shop.slots.forEach((s, i) => {
      if (acted || !s || s.sold || s.kind !== 'chess' || freeHand(ps) <= 0) return;
      const n = nameOf(gd, s.id); const price = ps.priceOf(s);
      if (!((counter && CHEAP.has(n) && price <= 1 && ps.funds >= 1) || (n === '吉星' && m.round <= 9 && ps.funds >= price + 2))) return;
      const before = new Set(ps.allChess().map((p) => p.uid));
      if (!ps.buy(i)?.ok) return;
      const fresh = ps.allChess().find((p) => !before.has(p.uid) && nameOf(gd, p.id) === n && !gd.isGolden(p.id));
      if (fresh && !weightOf(ps, n)) ps.sell(fresh.uid);
      acted = true;
    });
    if (!acted) break;
  }
}
const UW = env('OPUS2_UNIT', 3), UC = env('OPUS2_CARRYU', 20), UB = env('OPUS2_BOSSU', 60);
setUnitBonus((m, ps, c) => {
  if (!ps.botStrategyState?.opus2) return 0;
  const w = weightOf(ps, c.name); const x = OPUS_CARRY[c.name]; const k = c.isGolden ? 1 : 0.45;
  if (BENCH_ENGINE.has(c.name) && m.round < m.gd.bossRound) return -env('OPUS2_BENCHPEN', 40);
  let v = w * UW + (m.round < m.gd.bossRound ? env('OPUS2_STKU', 0) * stackOf(ps, c.name) * (c.isGolden ? 2 : 1) : 0);
  if (x) v += (m.round >= m.gd.bossRound ? UB * (x.boss || 0) : UC * (m.round >= 8 ? x.late : x.mid)) * k;
  return v;
});
const prep = createBotPrepSteps({ buyLoopSteps, levelUp, sellJunk, maybeFreeze, spendDown: () => {} });
const BAND_PREF = (process.env.OPUS_BANDS || 'band_sarkazb,band_amiya,band_ioleta,band_weakness,band_bonus,band_upgrade,band_copy,band_high,band_cannot,band_equipment').split(',').filter(Boolean);
export const OPUS2_BOT = Object.freeze({
  id: 'opus2',
  pickBand(m, ps) {
    const gd = ps?.gd || m.gd; const offered = new Set(gd.bandIds());
    // 按分工选开局：萨尔贡座位（中期零漏怪联防）先拿加攻击的，莱塔尼亚座位（要活到 R14 打领袖）先拿 40 血的歌利亚
    const pref = env('OPUS2_BANDROLE', 0) && ps.seat % 2 === 0 ? ['band_amiya', 'band_ioleta', 'band_weakness', ...BAND_PREF] : BAND_PREF;
    for (const id of pref) {
      if (!offered.has(id) || (m.bandTaken && m.bandTaken(id, ps.playerId))) continue;
      if (gd.bandBondIds(id).some((b) => gd.modeInactiveBonds.has(b))) continue;
      return id;
    }
    return botPickBand(m, ps);
  },
  *prepBegin(m, ps, opts) {
    if (!ps.alive || ps.ready) return null;
    chooseComp(m, ps);
    try { tricks(m, ps); } catch { /* 特化动作失败不影响主流程 */ }
    return yield* prep.prepBegin(m, ps, opts);
  },
  *prepEnd(m, ps, job) { return yield* prep.prepEnd(m, ps, job); },
});
