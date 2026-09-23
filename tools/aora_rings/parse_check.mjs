// 第一步验证：解析 aora rings.js，检查每组眼环能否用蛇形三角条带剖分
// （固定索引 (i, i+1, n-i...) series，无翻转三角形、面积守恒）。
import { readFileSync } from 'node:fs';

const AORA = process.env.AORA_PATH;
if (!AORA) throw new Error('set AORA_PATH to the aora-bot checkout');

function loadWindowScript(path) {
  const src = readFileSync(path, 'utf8');
  const window = {};
  new Function('window', src)(window);
  return window;
}

const rings = loadWindowScript(`${AORA}/emotion-ball/js/rings.js`).EB_RINGS;
console.log('HEAD_C =', rings.HEAD_C, 'EYE_HALF =', rings.EYE_HALF, 'rings =', rings.EXPRESSIONS.length);

// 蛇形条带索引：0,1,47, 1,2,47? 标准 strip: 顺序 v[] = 0,1,n-1,2,n-2,3,...
function stripTriangles(n) {
  const order = [0];
  let lo = 1, hi = n - 1, takeLo = true;
  while (lo <= hi) {
    order.push(takeLo ? lo++ : hi--);
    takeLo = !takeLo;
  }
  const tris = [];
  for (let i = 2; i < order.length; i++) tris.push([order[i - 2], order[i - 1], order[i]]);
  return tris;
}

const signedArea = (pts) => {
  let a = 0;
  for (let i = 0; i < pts.length; i++) {
    const [x1, y1] = pts[i], [x2, y2] = pts[(i + 1) % pts.length];
    a += x1 * y2 - x2 * y1;
  }
  return a / 2;
};
const triArea = (a, b, c) => ((b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1])) / 2;

let bad = 0;
rings.EXPRESSIONS.forEach((pair, ri) => {
  pair.forEach((ring, side) => {
    const n = ring.length;
    const polyA = Math.abs(signedArea(ring));
    const tris = stripTriangles(n);
    let sum = 0, flips = 0;
    const sign = Math.sign(triArea(...tris[0].map((i) => ring[i])) || 1);
    for (const t of tris) {
      const a = triArea(...t.map((i) => ring[i]));
      sum += Math.abs(a);
      if (Math.sign(a) !== sign && Math.abs(a) > 0.01) flips++;
    }
    const err = Math.abs(sum - polyA) / polyA;
    const ok = flips === 0 && err < 0.02;
    if (!ok) {
      bad++;
      console.log(`ring ${ri} side ${side}: n=${n} flips=${flips} areaErr=${(err * 100).toFixed(1)}%`);
    }
  });
});
console.log(bad === 0 ? '全部 50 条环蛇形剖分通过' : `${bad} 条环需要特殊处理`);
