import { useEffect, useRef } from "react";

type Particle = {
  /** Normalized radius 0..1 inside the lobed envelope */
  ring: number;
  phase: number;
  spinBias: number;
  arm: number;
  wobble: number;
  wobbleSpeed: number;
  r: number;
  hue: number;
  sat: number;
  light: number;
  alpha: number;
};

/**
 * Fixed-viewport pigment swirl in the main board well.
 * Soft saturated watercolor mass — not additive glow.
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
    let particles: Particle[] = [];
    let t0 = performance.now();
    let railW = 220;

    // Pigment inks — mid lightness so hue survives soft-light over wash
    const palette = [
      { hue: 312, sat: 58, light: 46 }, // plum
      { hue: 338, sat: 52, light: 48 }, // rose madder
      { hue: 198, sat: 48, light: 44 }, // dilute cyan
      { hue: 28, sat: 55, light: 48 }, // ochre
      { hue: 168, sat: 42, light: 42 }, // teal wash
      { hue: 268, sat: 50, light: 48 }, // violet
    ];

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

    const clusterGeom = () => {
      const wellL = railW;
      const wellW = Math.max(320, w - wellL);
      const cx = wellL + wellW * 0.5;
      const cy = h * 0.44;
      const bodyR = Math.min(wellW, h) * 0.28;
      return { cx, cy, bodyR };
    };

    /** Organic lobed silhouette — watercolor swirl, not a round ball. */
    const lobeRadius = (theta: number, bodyR: number, spin: number) => {
      const a = theta + spin;
      const lobes =
        0.62 +
        0.22 * Math.sin(2 * a) +
        0.12 * Math.sin(3 * a + 0.7) +
        0.08 * Math.cos(5 * a - 0.4) +
        0.05 * Math.sin(a * 0.5 + 1.1);
      return bodyR * Math.max(0.28, lobes);
    };

    const seed = () => {
      const { bodyR } = clusterGeom();
      const count = Math.max(72, Math.min(110, Math.floor((bodyR * bodyR) / 260)));
      particles = Array.from({ length: count }, (_, i) => {
        const c = palette[i % palette.length];
        const ring = Math.pow(Math.random(), 1.55);
        return {
          ring,
          phase: Math.random() * Math.PI * 2,
          spinBias: (0.015 + Math.random() * 0.04) * (Math.random() < 0.45 ? -1 : 1),
          arm: Math.floor(Math.random() * 3),
          wobble: bodyR * (0.008 + Math.random() * 0.025),
          wobbleSpeed: 0.35 + Math.random() * 0.45,
          r: bodyR * (0.08 + Math.random() * 0.16) * (1.05 - ring * 0.4),
          hue: c.hue + (Math.random() * 14 - 7),
          sat: c.sat + (Math.random() * 8 - 4),
          light: c.light + (Math.random() * 6 - 3),
          alpha: 0.1 + Math.random() * 0.12 * (1.15 - ring),
        };
      });
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

    const paintWashBody = (cx: number, cy: number, bodyR: number, spin: number, t: number) => {
      const breath = 1 + 0.025 * Math.sin(t * 0.35);
      // Soft pigment pool — source-over, muted alpha, no additive blowout
      const steps = 48;
      ctx.beginPath();
      for (let i = 0; i <= steps; i++) {
        const th = (i / steps) * Math.PI * 2;
        const rr = lobeRadius(th, bodyR, spin) * breath;
        const x = cx + Math.cos(th) * rr;
        const y = cy + Math.sin(th) * rr * 0.9;
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      }
      ctx.closePath();

      const pool = ctx.createRadialGradient(cx, cy, 0, cx, cy, bodyR * 1.05);
      pool.addColorStop(0, "hsla(318 48% 46% / 0.22)");
      pool.addColorStop(0.35, "hsla(200 40% 44% / 0.14)");
      pool.addColorStop(0.65, "hsla(28 45% 48% / 0.09)");
      pool.addColorStop(1, "hsla(280 35% 48% / 0)");
      ctx.fillStyle = pool;
      ctx.fill();

      // Quiet inner stain — no hot white core
      const stain = ctx.createRadialGradient(cx - bodyR * 0.1, cy + bodyR * 0.05, 0, cx, cy, bodyR * 0.45);
      stain.addColorStop(0, "hsla(330 42% 42% / 0.16)");
      stain.addColorStop(0.55, "hsla(270 38% 44% / 0.08)");
      stain.addColorStop(1, "hsla(270 30% 45% / 0)");
      ctx.fillStyle = stain;
      ctx.beginPath();
      ctx.ellipse(cx, cy, bodyR * 0.42, bodyR * 0.36, spin * 0.3, 0, Math.PI * 2);
      ctx.fill();
    };

    const paintFrame = (t: number) => {
      ctx.globalCompositeOperation = "source-over";
      ctx.clearRect(0, 0, w, h);
      // Keep source-over — additive "lighter" washed hues to white

      const { cx, cy, bodyR } = clusterGeom();
      const clusterSpin = t * 0.09;
      const breath = 1 + 0.02 * Math.sin(t * 0.38);

      paintWashBody(cx, cy, bodyR, clusterSpin, t);

      for (const p of particles) {
        const ang = p.phase + clusterSpin + t * p.spinBias + p.arm * 0.35;
        const envelope = lobeRadius(ang, bodyR, clusterSpin);
        const wob = Math.sin(t * p.wobbleSpeed + p.phase) * p.wobble;
        const rad = Math.min(envelope * 0.92, p.ring * envelope + wob) * breath;
        const x = cx + Math.cos(ang) * rad;
        const y = cy + Math.sin(ang) * rad * 0.9;
        const pulse = 0.94 + 0.06 * Math.sin(t * 0.7 + p.phase);
        drawBlob(ctx, x, y, p.r * pulse, p.hue, p.sat, p.light, p.alpha);
      }
    };

    const tick = (now: number) => {
      if (!running) return;
      paintFrame((now - t0) / 1000);
      raf = requestAnimationFrame(tick);
    };

    resize();
    window.addEventListener("resize", resize);

    if (reduceMotion) {
      paintFrame(0);
    } else {
      raf = requestAnimationFrame(tick);
    }

    return () => {
      running = false;
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", resize);
    };
  }, []);

  return <canvas ref={canvasRef} className="pigment-turbulence" aria-hidden="true" />;
}

function drawBlob(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  r: number,
  hue: number,
  sat: number,
  light: number,
  alpha: number,
) {
  const g = ctx.createRadialGradient(x, y, 0, x, y, r);
  g.addColorStop(0, `hsla(${hue} ${sat}% ${light}% / ${alpha})`);
  g.addColorStop(0.45, `hsla(${hue + 6} ${sat - 4}% ${light + 2}% / ${alpha * 0.45})`);
  g.addColorStop(1, `hsla(${hue} ${sat}% ${light}% / 0)`);
  ctx.fillStyle = g;
  ctx.beginPath();
  ctx.arc(x, y, r, 0, Math.PI * 2);
  ctx.fill();
}
