// SPDX-FileCopyrightText: 2026 sefuzhou770801-hub
// SPDX-License-Identifier: BSL-1.0
//
// aora-bot (github.com/sam70361/aora-bot) emotion-ball 眼环数据转换器。
// 读上游 rings.js / emotions.js（路径经 AORA_PATH 提供，上游源码不入仓），
// 把我们 15 个表情映射到的状态的眼环轮廓平移缩放到本机屏幕坐标系
// （球心 160,120，半径 100），连同轮换/开合/动画参数生成
// main/aora_ring_data.hpp。上游更新后重跑本脚本再编译即可同步。
//
// 用法: AORA_PATH=/path/to/aora-bot node tools/aora_rings/convert.mjs
import { readFileSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { execSync } from 'node:child_process';

const AORA = process.env.AORA_PATH;
if (!AORA) throw new Error('set AORA_PATH to the aora-bot checkout');
const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../..');

function loadWindowScript(path) {
  const window = {};
  new Function('window', readFileSync(path, 'utf8'))(window);
  return window;
}

const RINGS = loadWindowScript(`${AORA}/emotion-ball/js/rings.js`).EB_RINGS;
const SEED = loadWindowScript(`${AORA}/emotion-ball/js/emotions.js`).EMOTION_SEED;
const byId = new Map(SEED.map((e) => [e.id, e]));
let upstream = 'unknown';
try {
  upstream = execSync(`git -C ${AORA} rev-parse --short HEAD`).toString().trim();
} catch {}

// 我们的表情枚举 → aora 状态 id
const MAP = [
  ['Neutral', '02'], ['Happy', '10'], ['Sad', '12'], ['Angry', '21'],
  ['Doubt', '11'], ['Sleepy', '00'], ['Listening', '35'], ['Thinking', '30'],
  ['Excited', '33'], ['Curious', '03'], ['Confused', '20'], ['Surprised', '13'],
  ['Dizzy', '17'], ['Affection', '14'], ['Bored', '04'],
];

// 池覆盖：'14' 害羞去掉 ring 0（普通斜杠眼混在里面读不出害羞，
// 2026-08-30 维护者「摸摸应该是害羞状态」），只留羞怯 24 与闭合 13。
const POOL_OVERRIDE = { '14': [24, 13] };
// 全局剔除环：ring 8 是八字短杠眼（两眼主轴 -55°/+74° 相对倾斜），
// 2026-08-30 维护者发真机截图定案「不好看，删了」——待机与思考池都含它。
const BANNED_RINGS = [8];
const poolOf = (id) => {
  const base = POOL_OVERRIDE[id] ?? byId.get(id).pool;
  const filtered = base.filter((r) => !BANNED_RINGS.includes(r));
  return filtered.length > 0 ? filtered : base.slice(0, 1);
};

// 缩放：aora 头心 HEAD_C，把所有用到的环装进我们的球（r=100，留边）。
const HEAD = RINGS.HEAD_C;
const usedRings = [...new Set(MAP.flatMap(([, id]) => poolOf(id)))].sort((a, b) => a - b);
let maxD = 0;
for (const ri of usedRings) {
  for (const ring of RINGS.EXPRESSIONS[ri]) {
    for (const [x, y] of ring) {
      maxD = Math.max(maxD, Math.hypot(x - HEAD, y - HEAD));
    }
  }
}
const SCALE = 88 / maxD; // 环最远点落在半径 88，眼睛不顶球边
console.log(`used rings: ${usedRings.join(',')}  maxD=${maxD.toFixed(1)}  scale=${SCALE.toFixed(3)}`);

const ringIndex = new Map(usedRings.map((r, i) => [r, i]));

// ---- NEUTRAL 摆正副本（2026-08-30 维护者：常态不要倒八字眼）----
// aora 的平静环带歪头姿态：两眼同向斜、一高一低，黑底白圆上读成倒霉相。
// 给待机池生成矫正副本：各眼绕质心把长轴转竖直、两眼等高、左右对称于
// 球心，轮廓形状（圆润度/粗细/长短）保持 aora 原样。其他表情用原环。
function uprightCopy(pair, forceMidY) {
  const centered = pair.map((ring) => {
    let cx = 0, cy = 0;
    for (const [x, y] of ring) { cx += x; cy += y; }
    cx /= ring.length; cy /= ring.length;
    // PCA 主轴：比较两个候选方向的方差，取长轴
    let sxx = 0, syy = 0, sxy = 0;
    for (const [x, y] of ring) {
      const dx = x - cx, dy = y - cy;
      sxx += dx * dx; syy += dy * dy; sxy += dx * dy;
    }
    const theta = 0.5 * Math.atan2(2 * sxy, sxx - syy);
    const varAlong = (a) => {
      const c = Math.cos(a), s = Math.sin(a);
      return c * c * sxx + 2 * c * s * sxy + s * s * syy;
    };
    const longAxis = varAlong(theta) >= varAlong(theta + Math.PI / 2) ? theta : theta + Math.PI / 2;
    const rot = Math.PI / 2 - longAxis; // 长轴转到竖直
    const cr = Math.cos(rot), sr = Math.sin(rot);
    const pts = ring.map(([x, y]) => {
      const dx = x - cx, dy = y - cy;
      return [dx * cr - dy * sr, dx * sr + dy * cr];
    });
    return { cx, cy, pts };
  });
  const gap = Math.abs(centered[1].cx - centered[0].cx);
  const midY = forceMidY ?? (centered[0].cy + centered[1].cy) / 2;
  return {
    midY,
    pair: centered.map((eye, side) => {
      const nx = HEAD + (side === 0 ? -gap / 2 : gap / 2);
      return eye.pts.map(([dx, dy]) => [nx + dx, midY + dy]);
    }),
  };
}

// 副本统一用第一环的高度：轮换只变形状（粗细长短），眼位不上下跳。
// 2026-08-30 晚：摆正副本停用，待机回用 aora 原版环。维护者以 aora 展示站
// 为基准（「眼睛看向的角度不一致」），原版环的倾斜与位置本身承载视线方向。
// uprightCopy 保留备用：若倒八字观感复发，改用「整体拉平」变体再议。
const uprightRows = [];
const uprightIndexBySrc = new Map();
// C++ f32 字面量：必须带小数点（77f 非法，77.0f 合法）。
const f = (v) => {
  let s = v.toFixed(2).replace(/(\.\d*?)0+$/, '$1').replace(/\.$/, '');
  if (s === '-0') s = '0';
  if (!s.includes('.')) s += '.0';
  return s;
};

// 环数据：屏幕坐标（球心 160,120）
const emitPair = (pair, label) => {
  const sides = pair.map((ring) =>
    ring.map(([x, y]) => `${f(160 + (x - HEAD) * SCALE)}f, ${f(120 + (y - HEAD) * SCALE)}f`).join(', ')
  );
  return `    { // ${label}\n        {${sides[0]}},\n        {${sides[1]}},\n    },`;
};
const ringRows = usedRings.map((ri) => emitPair(RINGS.EXPRESSIONS[ri], `aora ring ${ri}`));
for (const u of uprightRows) {
  ringRows.push(emitPair(u.pair, `aora ring ${u.src} 摆正副本（NEUTRAL 专用）`));
}

// 表情配置
const ANIM_KIND = { sine: 0, glance: 1, jitter: 2, scan: 3 };
const TARGET = { eyes: 0, left: 1, right: 2 };
const cfgRows = MAP.map(([name, id]) => {
  const e = byId.get(id);
  const srcPool = poolOf(id);
  const pool = srcPool.map((r) => ringIndex.get(r));
  while (pool.length < 6) pool.push(pool[0]);
  const poolMs = e.poolMs ? (e.poolMs[0] + e.poolMs[1]) / 2 : 6000;
  const openness = e.openness ?? 1;
  const eyes = e.eyes ?? {};
  const off = (side, axis) => {
    const both = eyes.both ?? {};
    const own = eyes[side] ?? {};
    const key = axis === 0 ? ['x', 'lookX'] : ['y', 'lookY'];
    return (both[key[0]] ?? 0) + (both[key[1]] ?? 0) + (own[key[0]] ?? 0) + (own[key[1]] ?? 0);
  };
  const anims = (e.anims ?? [])
    .filter((a) => (a.target === 'eyes' || a.target === 'left' || a.target === 'right') &&
                   (a.prop === 'lookX' || a.prop === 'lookY' || a.prop === 'x' || a.prop === 'y') &&
                   ANIM_KIND[a.type] !== undefined)
    .slice(0, 3)
    .map((a) => {
      const axis = a.prop === 'lookX' || a.prop === 'x' ? 0 : 1;
      const period = a.period ?? (a.speed ? 1000 / a.speed : 1000);
      const phase = a.phaseMs ?? (a.phase ? (a.phase / (2 * Math.PI)) * period : 0);
      return `{${ANIM_KIND[a.type]}, ${axis}, ${TARGET[a.target]}, ${f(a.amp * SCALE)}f, ${f(period)}f, ${f(phase)}f}`;
    });
  while (anims.length < 3) anims.push('{0, 0, 0, 0.0f, 1.0f, 0.0f}');
  return `    { /* ${name} <- aora ${id} ${e.name} */\n` +
         `        {${pool.join(', ')}}, ${srcPool.length}, ${f(poolMs)}f, ${f(openness)}f,\n` +
         `        ${f(off('left', 0) * SCALE)}f, ${f(off('left', 1) * SCALE)}f, ` +
         `${f(off('right', 0) * SCALE)}f, ${f(off('right', 1) * SCALE)}f,\n` +
         `        {${anims.join(',\n         ')}}, ${(e.anims ?? []).filter((a) => TARGET[a.target] !== undefined && ANIM_KIND[a.type] !== undefined && ['lookX','lookY','x','y'].includes(a.prop)).slice(0,3).length},\n    },`;
});

const hpp = `// SPDX-FileCopyrightText: 2026 sam70361 (Emotion Ball eye-ring and emotion data)
// SPDX-FileCopyrightText: 2026 sefuzhou770801-hub (conversion)
// SPDX-License-Identifier: LicenseRef-Emotion-Ball-Community
//
// 本文件由 tools/aora_rings/convert.mjs 生成，勿手改。
// 数据来源：aora-bot (github.com/sam70361/aora-bot) emotion-ball
// rings.js / emotions.js，上游 commit ${upstream}。眼环轮廓与行为参数
// 按 Emotion Ball 社区许可（非商业）使用；许可全文、版权声明与 NOTICE.md
// 见 third_party/emotion-ball/。球形角色形象（身体造型/配色）未移植。
#pragma once

#include <cstdint>

namespace stackchan::app::aora {

inline constexpr std::size_t kRingPoints = 48;
inline constexpr std::size_t kRingCount = ${usedRings.length + uprightRows.length};

// 每环：左右眼各 48 点屏幕坐标 (x0,y0,x1,y1,...)，球心 160,120。
inline constexpr float kRings[kRingCount][2][kRingPoints * 2] = {
${ringRows.join('\n')}
};

// anims: kind 0=sine 1=glance 2=jitter 3=scan; axis 0=x 1=y;
// target 0=both 1=left 2=right。amp 已按环缩放折算成屏幕像素。
struct AnimCfg {
    std::uint8_t kind;
    std::uint8_t axis;
    std::uint8_t target;
    float amp;
    float period_ms;
    float phase_ms;
};

struct ExprCfg {
    std::uint8_t pool[6];
    std::uint8_t pool_n;
    float pool_ms;
    float openness;
    float left_dx, left_dy, right_dx, right_dy;
    AnimCfg anims[3];
    std::uint8_t anim_n;
};

// 下标 = stackchan::avatar::Expression 枚举值（0..14）。
inline constexpr ExprCfg kExprCfg[15] = {
${cfgRows.join('\n')}
};

} // namespace stackchan::app::aora
`;

writeFileSync(resolve(ROOT, 'main/aora_ring_data.hpp'), hpp);
console.log(`main/aora_ring_data.hpp written: ${usedRings.length} rings, 15 expr cfgs`);
