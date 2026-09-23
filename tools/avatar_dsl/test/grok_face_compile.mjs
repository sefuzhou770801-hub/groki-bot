// SPDX-FileCopyrightText: 2026 sefuzhou770801-hub
// SPDX-License-Identifier: BSL-1.0
//
// Compile-seam smoke for assets/grok_face.avdsl: the file must compile, stay
// under the 32 KiB NVS cap, and still emit both circles (idle body) and
// triangles (eyes + morphing body).
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { compile } from '../compile.js';
import { MAGIC, VERSION, Op, Var, SymbolicConsts } from '../opcodes.js';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../../..');
const src = readFileSync(resolve(ROOT, 'assets/grok_face.avdsl'), 'utf8');
// eye_open 不再出现：眨眼/开合压扁在 C++ 合成器（main/aora_face.hpp）里做。
for (const token of ['now_ms', 'mouth_open', 'expr_from', 'expr_blend',
                     'expr_hold_to', 'expr_hold_blend']) {
  if (!src.includes(token)) throw new Error(`grok_face.avdsl missing ${token}`);
}
if (src.includes('0.8369')) {
  throw new Error('grok_face.avdsl must not keep the egg body morph (0.8369): body stays a circle');
}
if (src.includes('0.7819')) {
  throw new Error('grok_face.avdsl must not keep the triangle body morph (0.7819)');
}
// aora 环眼：两只眼各 46 个三角形，覆盖 ring_l0..47 / ring_r0..47 全部坐标
for (const side of ['l', 'r']) {
  for (const i of [0, 1, 24, 46, 47]) {
    if (!src.includes(`ring_${side}${i}x`) || !src.includes(`ring_${side}${i}y`)) {
      throw new Error(`grok_face.avdsl missing ring_${side}${i} coordinates`);
    }
  }
}
if (SymbolicConsts.NEUTRAL !== 0 || SymbolicConsts.IDLE !== 0) {
  throw new Error('Neutral/Idle must stay 0 (extended Idle)');
}
if (SymbolicConsts.SLEEPY !== 5) {
  throw new Error('original six must keep Sleepy = 5');
}
const kkFaces = {
  LISTENING: 6, THINKING: 7, EXCITED: 8, CURIOUS: 9,
  CONFUSED: 10, SURPRISED: 11, DIZZY: 12, AFFECTION: 13, BORED: 14,
};
for (const [name, value] of Object.entries(kkFaces)) {
  if (SymbolicConsts[name] !== value) {
    throw new Error(`${name} must be ${value}, got ${SymbolicConsts[name]}`);
  }
  if (!src.includes(name)) {
    throw new Error(`grok_face.avdsl missing ${name} keyframe`);
  }
}
const buf = compile(src);
const dv = new DataView(buf);
const u8 = new Uint8Array(buf);

if (dv.getUint32(0, true) !== MAGIC) throw new Error('bad magic');
if (dv.getUint16(4, true) !== VERSION) throw new Error('bad version');
const constCount = dv.getUint16(8, true);
const fnCount = dv.getUint16(10, true);
const codeSize = dv.getUint16(12, true);
if (buf.byteLength > 32768) {
  throw new Error(`bytecode ${buf.byteLength} exceeds 32 KiB NVS cap`);
}
if (fnCount < 1) throw new Error('expected at least fn draw');

let off = 16;
for (let i = 0; i < constCount; ++i) {
  const tag = u8[off++];
  if (tag === 1 || tag === 2) off += 4;
  else if (tag === 3) off += 2;
  else throw new Error(`unknown const tag ${tag}`);
}
const codeOff = off + fnCount * 6;
const code = u8.subarray(codeOff, codeOff + codeSize);
let sawCircle = false;
let sawTriangle = false;
let sawExprFrom = false;
let sawExprBlend = false;
let sawExprHoldTo = false;
let sawExprHoldBlend = false;
let pc = 0;
while (pc < code.length) {
  const op = code[pc++];
  if (op === Op.FillCircle) sawCircle = true;
  if (op === Op.FillTriangle) sawTriangle = true;
  if (op === Op.PushVar) {
    const id = code[pc];
    if (id === Var.expr_from) sawExprFrom = true;
    if (id === Var.expr_blend) sawExprBlend = true;
    if (id === Var.expr_hold_to) sawExprHoldTo = true;
    if (id === Var.expr_hold_blend) sawExprHoldBlend = true;
  }
  if (op === Op.PushF32) pc += 4;
  else if (op === Op.PushI8 || op === Op.PushConst || op === Op.PushVar ||
           op === Op.PushLocal || op === Op.StoreLocal || op === Op.Call) pc += 1;
  else if (op === Op.PushI16 || op === Op.Jmp || op === Op.Jz || op === Op.Jnz) pc += 2;
}
if (!sawCircle) throw new Error('idle body should still emit fill_circle');
if (!sawTriangle) throw new Error('eyes/morph body should emit fill_triangle');
if (!sawExprFrom) throw new Error('blend path should PushVar expr_from');
if (!sawExprBlend) throw new Error('blend path should PushVar expr_blend');
if (!sawExprHoldTo) throw new Error('blend path should PushVar expr_hold_to');
if (!sawExprHoldBlend) throw new Error('blend path should PushVar expr_hold_blend');

console.log({ size: buf.byteLength, constCount, fnCount, codeSize, sawCircle, sawTriangle });
console.log('OK');
