import { useEffect, useRef } from "react";

type HexCell = {
  x: number;
  y: number;
  nx: number;
  ny: number;
};

/**
 * Silk watercolor nebula — pastel veil + whispered hex grain.
 * Tuned to nest into #ebe4ef paper: long falloff, slow drift,
 * dual blur + CSS mask so nothing reads as a hard sticker.
 */
export function PigmentTurbulence() {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d", { alpha: true });
    if (!ctx) return;

    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let raf = 0;
    let running = true;
    let w = 0;
    let h = 0;
    let dpr = 1;
    let cells: HexCell[] = [];
    let hexR = 3.6;
    let hexPath: Path2D | null = null;
    let off: HTMLCanvasElement | null = null;
    let octx: CanvasRenderingContext2D | null = null;
    let t0 = performance.now();
    let lastPaint = 0;
    let railW = 220;
    let cx = 0;
    let cy = 0;
    let fieldRx = 0;
    let fieldRy = 0;

    const readRail = () => {
      const rail = document.querySelector(".app--immersive .rail") as HTMLElement | null;
      if (rail) {
        const r = rail.getBoundingClientRect();
        railW = Math.max(160, Math.floor(r.width + r.left));
      } else {
        const raw = getComputedStyle(document.documentElement).getPropertyValue("--rail-w").trim();
        const n = Number.parseFloat(raw);
        railW = Number.isFinite(n) ? n + 14 : 234;
      }
    };

    const buildHexPath = (r: number) => {
      const p = new Path2D();
      for (let i = 0; i < 6; i++) {
        const a = (Math.PI / 180) * (60 * i - 30);
        const x = Math.cos(a) * r;
        const y = Math.sin(a) * r;
        if (i === 0) p.moveTo(x, y);
        else p.lineTo(x, y);
      }
      p.closePath();
      return p;
    };

    const seed = () => {
      const wellL = railW;
      const wellW = Math.max(320, w - wellL);
      cx = wellL + wellW * 0.5;
      cy = h * 0.44;
      // Wide field — edge dissolve is the whole point of “不突兀”
      const body = Math.min(wellW, h) * 0.42;
      fieldRx = body * 1.7;
      fieldRy = body * 1.32;
      // Fine overlapping mesh → continuous grain after blur
      hexR = Math.max(2.8, Math.min(4.2, body / 72));
      hexPath = buildHexPath(hexR * 1.14);

      const stepX = hexR * 1.55;
      const stepY = hexR * Math.sqrt(3) * 0.82;
      const list: HexCell[] = [];
      const x0 = cx - fieldRx;
      const x1 = cx + fieldRx;
      const y0 = cy - fieldRy;
      const y1 = cy + fieldRy;

      let row = 0;
      for (let y = y0; y <= y1; y += stepY, row++) {
        const xOff = row % 2 === 0 ? 0 : stepX * 0.5;
        for (let x = x0 + xOff; x <= x1; x += stepX) {
          const dx = (x - cx) / fieldRx;
          const dy = (y - cy) / fieldRy;
          if (dx * dx + dy * dy > 1.12) continue;
          list.push({
            x,
            y,
            nx: (x - cx) * 0.0085,
            ny: (y - cy) * 0.0085,
          });
        }
      }
      cells = list;

      if (!off) {
        off = document.createElement("canvas");
        octx = off.getContext("2d", { alpha: true });
      }
      if (off && octx) {
        off.width = Math.floor(w * dpr);
        off.height = Math.floor(h * dpr);
        octx.setTransform(dpr, 0, 0, dpr, 0, 0);
      }
    };

    const resize = () => {
      readRail();
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      w = Math.max(1, window.innerWidth);
      h = Math.max(1, window.innerHeight);
      canvas.width = Math.floor(w * dpr);
      canvas.height = Math.floor(h * dpr);
      canvas.style.width = `${w}px`;
      canvas.style.height = `${h}px`;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      seed();
    };

    /** Dominant soft body — watercolor ink pool, not a dark disc. */
    const paintVeil = (c: CanvasRenderingContext2D, t: number) => {
      const breath = 1 + 0.02 * Math.sin(t * 0.22);
      const rx = fieldRx * 0.95 * breath;
      const ry = fieldRy * 0.95 * breath;

      const g = c.createRadialGradient(cx, cy, rx * 0.05, cx, cy, rx);
      g.addColorStop(0, "rgba(198, 168, 228, 0.34)");
      g.addColorStop(0.22, "rgba(178, 148, 218, 0.26)");
      g.addColorStop(0.45, "rgba(150, 158, 220, 0.16)");
      g.addColorStop(0.68, "rgba(168, 178, 220, 0.08)");
      g.addColorStop(0.86, "rgba(210, 200, 228, 0.035)");
      g.addColorStop(1, "rgba(235, 228, 239, 0)");
      c.fillStyle = g;
      c.beginPath();
      c.ellipse(cx, cy, rx, ry, 0, 0, Math.PI * 2);
      c.fill();

      // Slow orbiting secondary blot — depth without hard layers
      const ox = cx + Math.cos(t * 0.09) * fieldRx * 0.1;
      const oy = cy - Math.sin(t * 0.08) * fieldRy * 0.08;
      const g2 = c.createRadialGradient(ox, oy, 0, ox, oy, rx * 0.52);
      g2.addColorStop(0, "rgba(214, 150, 204, 0.14)");
      g2.addColorStop(0.5, "rgba(140, 170, 226, 0.07)");
      g2.addColorStop(1, "rgba(140, 170, 226, 0)");
      c.fillStyle = g2;
      c.beginPath();
      c.ellipse(ox, oy, rx * 0.52, ry * 0.44, t * 0.04, 0, Math.PI * 2);
      c.fill();

      // Cool mist wash on the lower-right — ties into paper blues
      const ox3 = cx + fieldRx * 0.18;
      const oy3 = cy + fieldRy * 0.12;
      const g3 = c.createRadialGradient(ox3, oy3, 0, ox3, oy3, rx * 0.4);
      g3.addColorStop(0, "rgba(130, 190, 220, 0.09)");
      g3.addColorStop(1, "rgba(130, 190, 220, 0)");
      c.fillStyle = g3;
      c.beginPath();
      c.ellipse(ox3, oy3, rx * 0.4, ry * 0.34, 0, 0, Math.PI * 2);
      c.fill();
    };

    const paintFrame = (t: number) => {
      if (!hexPath || !off || !octx || cells.length === 0) return;

      octx.globalCompositeOperation = "source-over";
      octx.clearRect(0, 0, w, h);
      paintVeil(octx, t);

      // Glacial cohesive drift — neighboring cells share motion
      const driftX = t * 0.038;
      const driftY = t * 0.026;
      const swirl = t * 0.028;
      const breath = 1 + 0.022 * Math.sin(t * 0.24);

      for (const cell of cells) {
        const dx = (cell.x - cx) / fieldRx;
        const dy = (cell.y - cy) / fieldRy;
        const rr = Math.sqrt(dx * dx + dy * dy);
        // Very long vignette — mesh fades long before the field edge
        const radial = Math.max(0, 1 - smoothstep(0.12, 0.96, rr / breath));
        if (radial < 0.04) continue;

        const ang = Math.atan2(dy, dx) + swirl * (0.35 + rr * 0.2);
        const rad = rr * (0.92 + 0.06 * Math.sin(ang * 1.6 + t * 0.16));
        const wx = Math.cos(ang) * rad * 1.7 + driftX;
        const wy = Math.sin(ang) * rad * 1.7 + driftY;

        const n1 = fbm(cell.nx + wx, cell.ny + wy, 3);
        const n2 = fbm(cell.nx * 1.55 - wy * 0.35, cell.ny * 1.55 + wx * 0.3, 2);
        // Narrow energy range → soft clouds, no harsh dark voids
        let v = n1 * 0.58 + n2 * 0.42;
        v = 0.42 + 0.58 * Math.pow(clamp01(v), 0.85);
        const core = Math.pow(Math.max(0, 1 - rr * 0.95), 2.0) * 0.12;
        const energy = clamp01(v * 0.82 + core) * radial;
        if (energy < 0.1) continue;

        const { r, g, b, a } = silkRgba(energy, n2, radial);
        octx.fillStyle = `rgba(${r},${g},${b},${a})`;
        octx.save();
        octx.translate(cell.x, cell.y);
        const s = 0.9 + energy * 0.18;
        octx.scale(s, s);
        octx.fill(hexPath);
        octx.restore();
      }

      // Soft composite blur — dissolves hex facets into continuous pigment
      ctx.globalCompositeOperation = "source-over";
      ctx.clearRect(0, 0, w, h);
      ctx.save();
      ctx.filter = "blur(2.4px)";
      ctx.globalAlpha = 0.92;
      ctx.drawImage(off, 0, 0, w, h);
      ctx.restore();
    };

    const tick = (now: number) => {
      if (!running) return;
      // ~30fps — smoother perceived motion, less hitch than 60fps heavy fill
      if (now - lastPaint >= 32) {
        lastPaint = now;
        paintFrame((now - t0) / 1000);
      }
      raf = requestAnimationFrame(tick);
    };

    resize();
    window.addEventListener("resize", resize);

    if (reduceMotion) paintFrame(0);
    else raf = requestAnimationFrame(tick);

    return () => {
      running = false;
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", resize);
    };
  }, []);

  return <canvas ref={canvasRef} className="pigment-turbulence" aria-hidden="true" />;
}

function clamp01(v: number) {
  return v < 0 ? 0 : v > 1 ? 1 : v;
}

function smoothstep(e0: number, e1: number, x: number) {
  const t = clamp01((x - e0) / (e1 - e0));
  return t * t * (3 - 2 * t);
}

function hash2(ix: number, iy: number) {
  const n = Math.sin(ix * 127.1 + iy * 311.7) * 43758.5453123;
  return n - Math.floor(n);
}

function valueNoise(x: number, y: number) {
  const x0 = Math.floor(x);
  const y0 = Math.floor(y);
  const fx = x - x0;
  const fy = y - y0;
  const ux = fx * fx * (3 - 2 * fx);
  const uy = fy * fy * (3 - 2 * fy);
  const a = hash2(x0, y0);
  const b = hash2(x0 + 1, y0);
  const c = hash2(x0, y0 + 1);
  const d = hash2(x0 + 1, y0 + 1);
  return a + (b - a) * ux + (c - a) * uy + (a - b - c + d) * ux * uy;
}

function fbm(x: number, y: number, octaves: number) {
  let amp = 0.5;
  let freq = 1;
  let sum = 0;
  let norm = 0;
  for (let i = 0; i < octaves; i++) {
    sum += amp * valueNoise(x * freq, y * freq);
    norm += amp;
    amp *= 0.5;
    freq *= 2.02;
  }
  return sum / norm;
}

/**
 * Pastel ramp locked to the lavender wash —
 * lilac → soft violet → rose → misty periwinkle → soft cyan.
 * Kept mid-light so it never punches a dirty hole in the paper.
 */
function silkRgba(energy: number, tint: number, radial: number) {
  const stops: Array<[number, number, number, number]> = [
    [0.0, 218, 208, 232],
    [0.2, 196, 174, 226],
    [0.4, 178, 148, 218],
    [0.55, 198, 142, 206],
    [0.72, 148, 166, 226],
    [0.88, 128, 192, 226],
    [1.0, 138, 208, 228],
  ];

  let r = stops[0][1];
  let g = stops[0][2];
  let b = stops[0][3];
  for (let i = 0; i < stops.length - 1; i++) {
    const a = stops[i];
    const c = stops[i + 1];
    if (energy >= a[0] && energy <= c[0]) {
      const t = (energy - a[0]) / (c[0] - a[0] || 1);
      const u = t * t * (3 - 2 * t);
      r = a[1] + (c[1] - a[1]) * u;
      g = a[2] + (c[2] - a[2]) * u;
      b = a[3] + (c[3] - a[3]) * u;
      break;
    }
    if (energy > c[0]) {
      r = c[1];
      g = c[2];
      b = c[3];
    }
  }

  r = Math.min(255, r + tint * 10);
  b = Math.min(255, b + (1 - tint) * 8);

  // Whisper alpha — veil carries the color, mesh only textures it
  const a = (0.06 + energy * 0.2) * (0.35 + radial * 0.65);
  return {
    r: Math.round(r),
    g: Math.round(g),
    b: Math.round(b),
    a: Math.min(0.32, a),
  };
}
