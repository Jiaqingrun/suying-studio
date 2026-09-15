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
      // Tight central cluster only — never fill the board
      const count = Math.max(28, Math.min(48, Math.floor((w * h) / 36000)));
      const maxOrbit = Math.min(w, h) * 0.14;
      particles = Array.from({ length: count }, (_, i) => {
        const c = palette[i % palette.length];
        // Strong core bias: most mass near center
        const ring = Math.pow(Math.random(), 1.35);
        return {
          orbit: maxOrbit * (0.08 + ring * 0.92),
          phase: Math.random() * Math.PI * 2,
          spin: (0.14 + Math.random() * 0.2) * (Math.random() < 0.4 ? -1 : 1),
          squash: 0.78 + Math.random() * 0.16,
          wobble: 2 + Math.random() * 6,
          wobbleSpeed: 0.4 + Math.random() * 0.5,
          r: 12 + Math.random() * 28 * (1 - ring * 0.5),
          hue: c.hue + (Math.random() * 14 - 7),
          sat: c.sat + (Math.random() * 8 - 4),
          light: c.light + (Math.random() * 6 - 3),
          alpha: 0.22 + Math.random() * 0.2,
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
      // Board well center — between the two AV cards
      cx: w * 0.5,
      cy: h * 0.42,
    });

    const paintFrame = (t: number) => {
      ctx.globalCompositeOperation = "source-over";
      ctx.clearRect(0, 0, w, h);
      ctx.globalCompositeOperation = "lighter";

      const { cx, cy } = clusterCenter();
      const clusterSpin = t * 0.1;
      const breath = 1 + 0.03 * Math.sin(t * 0.45);

      // Dense core bloom — reads as one pigment body
      const coreR = Math.min(w, h) * 0.12;
      const core = ctx.createRadialGradient(cx, cy, 0, cx, cy, coreR);
      core.addColorStop(0, "hsla(300 60% 62% / 0.22)");
      core.addColorStop(0.35, "hsla(210 55% 58% / 0.12)");
      core.addColorStop(0.7, "hsla(48 65% 55% / 0.06)");
      core.addColorStop(1, "hsla(300 50% 60% / 0)");
      ctx.fillStyle = core;
      ctx.beginPath();
      ctx.arc(cx, cy, coreR, 0, Math.PI * 2);
      ctx.fill();

      for (const p of particles) {
        const ang = p.phase + clusterSpin * Math.sign(p.spin) + t * p.spin * 0.2;
        const wob = Math.sin(t * p.wobbleSpeed + p.phase) * p.wobble;
        const rad = (p.orbit + wob) * breath;
        const x = cx + Math.cos(ang) * rad;
        const y = cy + Math.sin(ang) * rad * p.squash;
        drawBlob(ctx, x, y, p.r * (0.92 + 0.08 * Math.sin(t * 0.9 + p.phase)), p.hue, p.sat, p.light, p.alpha);
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
