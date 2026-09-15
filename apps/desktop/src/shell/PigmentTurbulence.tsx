import { useEffect, useRef } from "react";

type Particle = {
  /** Base orbit radius from cluster center */
  orbit: number;
  /** Angular phase around the cluster */
  phase: number;
  /** Angular speed (rad/s), signed */
  spin: number;
  /** Vertical squash of orbit ellipse */
  squash: number;
  /** Soft radial wobble */
  wobble: number;
  wobbleSpeed: number;
  r: number;
  hue: number;
  sat: number;
  light: number;
  alpha: number;
};

/**
 * Central pigment vortex behind the right-hand board.
 * Particles stay gathered as one slow-turning cluster — not scattered.
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

    const palette = [
      { hue: 295, sat: 72, light: 58 },
      { hue: 335, sat: 70, light: 62 },
      { hue: 195, sat: 74, light: 56 },
      { hue: 48, sat: 78, light: 58 },
      { hue: 162, sat: 64, light: 52 },
      { hue: 268, sat: 68, light: 64 },
    ];

    const seed = () => {
      // Tight cluster — count scales gently with board size but stays compact
      const count = Math.max(36, Math.min(64, Math.floor((w * h) / 28000)));
      const maxOrbit = Math.min(w, h) * 0.22;
      particles = Array.from({ length: count }, (_, i) => {
        const c = palette[i % palette.length];
        // Bias toward core: most mass near center, few outer filaments
        const ring = Math.pow(Math.random(), 0.55);
        return {
          orbit: maxOrbit * (0.12 + ring * 0.88),
          phase: Math.random() * Math.PI * 2,
          spin: (0.12 + Math.random() * 0.18) * (Math.random() < 0.35 ? -1 : 1),
          squash: 0.72 + Math.random() * 0.22,
          wobble: 4 + Math.random() * 10,
          wobbleSpeed: 0.35 + Math.random() * 0.55,
          r: 14 + Math.random() * 36 * (1 - ring * 0.45),
          hue: c.hue + (Math.random() * 16 - 8),
          sat: c.sat + (Math.random() * 8 - 4),
          light: c.light + (Math.random() * 6 - 3),
          alpha: 0.18 + Math.random() * 0.22,
        };
      });
    };

    const resize = () => {
      const parent = canvas.parentElement;
      if (!parent) return;
      const rect = parent.getBoundingClientRect();
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      w = Math.max(1, Math.floor(rect.width));
      h = Math.max(1, Math.floor(rect.height));
      canvas.width = Math.floor(w * dpr);
      canvas.height = Math.floor(h * dpr);
      canvas.style.width = `${w}px`;
      canvas.style.height = `${h}px`;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      seed();
    };

    const clusterCenter = () => ({
      // Slightly right of geometric center — sits in the open board well
      cx: w * 0.56,
      cy: h * 0.48,
    });

    const paintFrame = (t: number) => {
      ctx.globalCompositeOperation = "source-over";
      ctx.clearRect(0, 0, w, h);
      ctx.globalCompositeOperation = "lighter";

      const { cx, cy } = clusterCenter();
      // Whole cluster slowly breathes + turns
      const clusterSpin = t * 0.085;
      const breath = 1 + 0.04 * Math.sin(t * 0.4);

      // Soft core bloom so the mass reads as one body
      const core = ctx.createRadialGradient(cx, cy, 0, cx, cy, Math.min(w, h) * 0.2);
      core.addColorStop(0, "hsla(290 55% 62% / 0.16)");
      core.addColorStop(0.45, "hsla(210 50% 58% / 0.08)");
      core.addColorStop(1, "hsla(48 60% 55% / 0)");
      ctx.fillStyle = core;
      ctx.beginPath();
      ctx.arc(cx, cy, Math.min(w, h) * 0.2, 0, Math.PI * 2);
      ctx.fill();

      for (const p of particles) {
        const ang = p.phase + clusterSpin * p.spin + t * p.spin * 0.15;
        const wob = Math.sin(t * p.wobbleSpeed + p.phase) * p.wobble;
        const rad = (p.orbit + wob) * breath;
        const x = cx + Math.cos(ang) * rad;
        const y = cy + Math.sin(ang) * rad * p.squash;
        drawBlob(ctx, x, y, p.r * (0.9 + 0.1 * Math.sin(t * 0.8 + p.phase)), p.hue, p.sat, p.light, p.alpha);
      }
    };

    const tick = (now: number) => {
      if (!running) return;
      paintFrame((now - t0) / 1000);
      raf = requestAnimationFrame(tick);
    };

    resize();
    const ro = new ResizeObserver(resize);
    if (canvas.parentElement) ro.observe(canvas.parentElement);

    if (reduceMotion) {
      paintFrame(0);
    } else {
      raf = requestAnimationFrame(tick);
    }

    return () => {
      running = false;
      cancelAnimationFrame(raf);
      ro.disconnect();
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
  g.addColorStop(0.4, `hsla(${hue + 10} ${sat - 4}% ${light + 4}% / ${alpha * 0.4})`);
  g.addColorStop(1, `hsla(${hue} ${sat}% ${light}% / 0)`);
  ctx.fillStyle = g;
  ctx.beginPath();
  ctx.arc(x, y, r, 0, Math.PI * 2);
  ctx.fill();
}
