// SPDX-FileCopyrightText: 2026 sefuzhou770801-hub
// SPDX-License-Identifier: BSL-1.0
//
// Eye-ring data converter for aora-bot's (github.com/sam70361/aora-bot) emotion-ball.
// Reads the upstream rings.js / emotions.js (path from AORA_PATH; the upstream source is not in this repo),
// moves and scales the eye-ring contours of the states our 15 expressions map to into the device's screen
// coordinates (ball centre 160,120, radius 100), and writes them with the rotation / openness / animation
// parameters to main/aora_ring_data.hpp. After an upstream update, rerun this script and rebuild.
//
// Usage: AORA_PATH=/path/to/aora-bot node tools/aora_rings/convert.mjs
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

// Our expression enum → aora state id
const MAP = [
  ['Neutral', '02'], ['Happy', '10'], ['Sad', '12'], ['Angry', '21'],
  ['Doubt', '11'], ['Sleepy', '00'], ['Listening', '35'], ['Thinking', '30'],
  ['Excited', '33'], ['Curious', '03'], ['Confused', '20'], ['Surprised', '13'],
  ['Dizzy', '17'], ['Affection', '14'], ['Bored', '04'],
];

// Pool override: '14' (shy) drops ring 0 (a plain slanted eye that does not read as shy)
// and keeps only the bashful ring 24 and the closed ring 13, so petting reads as shy.
const POOL_OVERRIDE = { '14': [24, 13] };
// Rings excluded everywhere: ring 8 is a pair of short slanted bars (eye axes at -55°/+74°),
// which looks wrong on the device; the idle and thinking pools both contained it.
const BANNED_RINGS = [8];
const poolOf = (id) => {
  const base = POOL_OVERRIDE[id] ?? byId.get(id).pool;
  const filtered = base.filter((r) => !BANNED_RINGS.includes(r));
  return filtered.length > 0 ? filtered : base.slice(0, 1);
};

// Scale: around aora's head centre HEAD_C, fit every used ring into our ball (r=100, with a margin).
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
const SCALE = 88 / maxD; // the farthest ring point lands at radius 88, so the eyes never touch the ball's edge
console.log(`used rings: ${usedRings.join(',')}  maxD=${maxD.toFixed(1)}  scale=${SCALE.toFixed(3)}`);

const ringIndex = new Map(usedRings.map((r, i) => [r, i]));

// ---- Upright copies for NEUTRAL (currently not used, see below) ----
// aora's calm rings have a tilted-head pose: both eyes slant the same way, one higher than the other.
// uprightCopy makes corrected copies for the idle pool: each eye's long axis is rotated upright about its
// centroid, both eyes at the same height and mirrored about the centre, with aora's contour shape kept.
function uprightCopy(pair, forceMidY) {
  const centered = pair.map((ring) => {
    let cx = 0, cy = 0;
    for (const [x, y] of ring) { cx += x; cy += y; }
    cx /= ring.length; cy /= ring.length;
    // PCA main axis: compare the variance along both candidate directions and take the long axis
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
    const rot = Math.PI / 2 - longAxis; // rotate the long axis upright
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

// Copies share the first ring's height, so rotating rings changes the shape only and the eyes do not jump.
// The upright copies are currently off and the idle pool uses aora's original rings: their tilt and position
// carry the gaze direction, as on aora's own showcase site.
// uprightCopy is kept so the idle eyes can be levelled again if needed.
const uprightRows = [];
const uprightIndexBySrc = new Map();
// C++ float literals need a decimal point (77f is invalid, 77.0f is valid).
const f = (v) => {
  let s = v.toFixed(2).replace(/(\.\d*?)0+$/, '$1').replace(/\.$/, '');
  if (s === '-0') s = '0';
  if (!s.includes('.')) s += '.0';
  return s;
};

// Ring data: screen coordinates (ball centre 160,120)
const emitPair = (pair, label) => {
  const sides = pair.map((ring) =>
    ring.map(([x, y]) => `${f(160 + (x - HEAD) * SCALE)}f, ${f(120 + (y - HEAD) * SCALE)}f`).join(', ')
  );
  return `    { // ${label}\n        {${sides[0]}},\n        {${sides[1]}},\n    },`;
};
const ringRows = usedRings.map((ri) => emitPair(RINGS.EXPRESSIONS[ri], `aora ring ${ri}`));
for (const u of uprightRows) {
  ringRows.push(emitPair(u.pair, `aora ring ${u.src} upright copy (NEUTRAL only)`));
}

// Expression configuration
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
// Generated by tools/aora_rings/convert.mjs; do not edit by hand.
// Source: aora-bot (github.com/sam70361/aora-bot) emotion-ball
// rings.js / emotions.js, upstream commit ${upstream}. The eye-ring contours and behaviour parameters
// are used under the Emotion Ball Community License (non-commercial); the full license, copyright notice
// and NOTICE.md are in third_party/emotion-ball/. The ball character's design (body shape, colours) is not used.
#pragma once

#include <cstdint>

namespace stackchan::app::aora {

inline constexpr std::size_t kRingPoints = 48;
inline constexpr std::size_t kRingCount = ${usedRings.length + uprightRows.length};

// Per ring: 48 screen points for each eye (x0,y0,x1,y1,...), ball centre 160,120.
inline constexpr float kRings[kRingCount][2][kRingPoints * 2] = {
${ringRows.join('\n')}
};

// anims: kind 0=sine 1=glance 2=jitter 3=scan; axis 0=x 1=y;
// target 0=both 1=left 2=right. amp is already scaled to screen pixels.
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

// Index = stackchan::avatar::Expression value (0..14).
inline constexpr ExprCfg kExprCfg[15] = {
${cfgRows.join('\n')}
};

} // namespace stackchan::app::aora
`;

writeFileSync(resolve(ROOT, 'main/aora_ring_data.hpp'), hpp);
console.log(`main/aora_ring_data.hpp written: ${usedRings.length} rings, 15 expr cfgs`);
