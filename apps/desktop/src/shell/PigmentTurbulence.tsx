import { useEffect, useRef } from "react";

type HexCell = {
  x: number;
  y: number;
  /** unit offset in noise space */
  nx: number;
  ny: number;
};

/**
 * Fixed center nebula: fine hexagonal mesh tinted by layered noise.
 * Reference look — deep navy cells, violet/magenta clouds, cyan hotspots.
 * Soft radial falloff into the watercolor wash (no muddy multiply blot).
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
    let hexR = 5.5;
    let hexPath: Path2D | null = null;
    let t0 = performance.now();
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
      const body = Math.min(wellW, h) * 0.34;
      fieldRx = body * 1.35;
      fieldRy = body * 1.05;
      hexR = Math.max(4.2, Math.min(6.4, body / 48));
      hexPath = buildHexPath(hexR * 0.92);

      const stepX = hexR * 1.75;
      const stepY = hexR * Math.sqrt(3) * 0.92;
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
          if (dx * dx + dy * dy > 1.05) continue;
          list.push({
            x,
            y,
            nx: (x - cx) * 0.018,
            ny: (y - cy) * 0.018,
          });
        }
      }
      cells = list;
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

    const paintFrame = (t: number) => {
      ctx.globalCompositeOperation = "source-over";
      ctx.clearRect(0, 0, w, h);
      if (!hexPath || cells.length === 0) return;

      // Slow domain drift — one cohesive flowing mass
      const driftX = t * 0.11;
      const driftY = t * 0.07;
      const swirl = t * 0.085;
      const breath = 1 + 0.04 * Math.sin(t * 0.4);

      for (const cell of cells) {
        const dx = (cell.x - cx) / fieldRx;
        const dy = (cell.y - cy) / fieldRy;
        const rr = Math.sqrt(dx * dx + dy * dy);
        // Soft vignette — keep edges airy into the wash
        const radial = Math.max(0, 1 - smoothstep(0.42, 1.02, rr / breath));
        if (radial < 0.02) continue;

        // Polar swirl so the cloud turns as one body
        const ang = Math.atan2(dy, dx) + swirl * (0.55 + rr * 0.35);
        const rad = rr * (0.85 + 0.12 * Math.sin(ang * 2.2 + t * 0.3));
        const wx = Math.cos(ang) * rad * 3.2 + driftX;
        const wy = Math.sin(ang) * rad * 3.2 + driftY;

        const n1 = fbm(cell.nx * 1.1 + wx, cell.ny * 1.1 + wy, 4);
        const n2 = fbm(cell.nx * 2.4 - wy * 0.6, cell.ny * 2.4 + wx * 0.5, 3);
        const n3 = fbm(cell.nx * 0.55 + driftY * 0.4, cell.ny * 0.55 - driftX * 0.3, 3);
        // Turbulent density: bright cores on dark honeycomb
        let v = n1 * 0.55 + n2 * 0.28 + (1 - n3) * 0.17;
        v = Math.pow(clamp01(v), 1.15);
        // Punch a luminous core near center
        const core = Math.pow(Math.max(0, 1 - rr * 1.15), 1.6) * 0.35;
        v = clamp01(v * 0.78 + core + n2 * 0.12);

        const energy = v * radial;
        if (energy < 0.06) continue;

        const { r, g, b, a } = nebulaRgba(energy, n2, radial);
        ctx.fillStyle = `rgba(${r},${g},${b},${a})`;
        ctx.save();
        ctx.translate(cell.x, cell.y);
        ctx.fill(hexPath);
        ctx.restore();
      }
    };

    const tick = (now: number) => {
      if (!running) return;
      paintFrame((now - t0) / 1000);
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

/** Hash-based value noise — cheap, stable, good enough for honeycomb tint. */
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
 * Color ramp matching the hex nebula reference:
 * deep navy → royal violet → magenta → electric cyan.
 */
function nebulaRgba(energy: number, tint: number, radial: number) {
  // Stops as [e, r, g, b]
  const stops: Array<[number, number, number, number]> = [
    [0.0, 4, 6, 22],
    [0.18, 18, 12, 58],
    [0.36, 58, 24, 140],
    [0.52, 138, 36, 210],
    [0.68, 190, 55, 230],
    [0.82, 90, 140, 255],
    [1.0, 40, 240, 255],
  ];

  let r = stops[0][1];
  let g = stops[0][2];
  let b = stops[0][3];
  for (let i = 0; i < stops.length - 1; i++) {
    const a = stops[i];
    const c = stops[i + 1];
    if (energy >= a[0] && energy <= c[0]) {
      const t = (energy - a[0]) / (c[0] - a[0] || 1);
      r = a[1] + (c[1] - a[1]) * t;
      g = a[2] + (c[2] - a[2]) * t;
      b = a[3] + (c[3] - a[3]) * t;
      break;
    }
    if (energy > c[0]) {
      r = c[1];
      g = c[2];
      b = c[3];
    }
  }

  // Magenta bias in mid cloud, cyan bias on peaks
  if (energy > 0.35 && energy < 0.72) {
    r = Math.min(255, r + tint * 36);
    b = Math.min(255, b + (1 - tint) * 18);
  } else if (energy >= 0.72) {
    g = Math.min(255, g + tint * 28);
    b = Math.min(255, b + 20);
  }

  // Opaque enough in the core to read as honeycomb, soft on rim
  const a =
    energy < 0.2
      ? 0.18 + energy * 1.1
      : energy < 0.55
        ? 0.42 + energy * 0.55
        : 0.72 + energy * 0.22;
  return {
    r: Math.round(r),
    g: Math.round(g),
    b: Math.round(b),
    a: Math.min(0.94, a * (0.55 + radial * 0.55)),
  };
}
