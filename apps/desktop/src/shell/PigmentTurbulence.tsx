import { useEffect, useRef } from "react";

type Particle = {
  ring: number;
  phase: number;
  spinBias: number;
  arm: number;
  wobble: number;
  wobbleSpeed: number;
  rx: number;
  ry: number;
  hue: number;
  sat: number;
  light: number;
  alpha: number;
};

/**
 * Fixed pigment swirl: visible colored ink blots that turn as one lobed mass.
 * Multiply stain + dark mid-tones (no white cores, no additive glow).
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

    // Darker pigment so multiply actually stains the wash
    const palette = [
      { hue: 312, sat: 72, light: 36 },
      { hue: 345, sat: 68, light: 38 },
      { hue: 200, sat: 70, light: 34 },
      { hue: 28, sat: 74, light: 38 },
      { hue: 162, sat: 60, light: 32 },
      { hue: 272, sat: 66, light: 36 },
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
      return {
        cx: wellL + wellW * 0.5,
        cy: h * 0.44,
        bodyR: Math.min(wellW, h) * 0.28,
      };
    };

    const lobeRadius = (theta: number, bodyR: number, spin: number) => {
      const a = theta + spin;
      const lobes =
        0.55 +
        0.28 * Math.sin(2 * a + 0.15) +
        0.15 * Math.sin(3 * a - 0.6) +
        0.1 * Math.cos(5 * a + 0.9);
      return bodyR * Math.max(0.34, lobes);
    };

    const seed = () => {
      const { bodyR } = clusterGeom();
      const count = Math.max(70, Math.min(100, Math.floor((bodyR * bodyR) / 240)));
      particles = Array.from({ length: count }, (_, i) => {
        const c = palette[i % palette.length];
        const ring = Math.pow(Math.random(), 1.25);
        const base = bodyR * (0.14 + Math.random() * 0.2) * (1.05 - ring * 0.3);
        return {
          ring,
          phase: Math.random() * Math.PI * 2,
          spinBias: (0.02 + Math.random() * 0.05) * (Math.random() < 0.45 ? -1 : 1),
          arm: Math.floor(Math.random() * 3),
          wobble: bodyR * (0.012 + Math.random() * 0.028),
          wobbleSpeed: 0.35 + Math.random() * 0.5,
          rx: base,
          ry: base * (0.55 + Math.random() * 0.45),
          hue: c.hue + (Math.random() * 14 - 7),
          sat: c.sat + (Math.random() * 8 - 4),
          light: c.light + (Math.random() * 5 - 2),
          alpha: 0.45 + Math.random() * 0.28 * (1.15 - ring),
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
      const breath = 1 + 0.03 * Math.sin(t * 0.35);
      const steps = 60;
      ctx.beginPath();
      for (let i = 0; i <= steps; i++) {
        const th = (i / steps) * Math.PI * 2;
        const rr = lobeRadius(th, bodyR, spin) * breath;
        const x = cx + Math.cos(th) * rr;
        const y = cy + Math.sin(th) * rr * 0.86;
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      }
      ctx.closePath();

      // Colored body — dark midtones, no pale center
      const pool = ctx.createRadialGradient(cx, cy, bodyR * 0.08, cx, cy, bodyR * 1.05);
      pool.addColorStop(0, "hsla(318 70% 38% / 0.55)");
      pool.addColorStop(0.3, "hsla(205 68% 36% / 0.48)");
      pool.addColorStop(0.55, "hsla(28 72% 38% / 0.4)");
      pool.addColorStop(0.78, "hsla(270 60% 40% / 0.28)");
      pool.addColorStop(1, "hsla(300 45% 42% / 0)");
      ctx.fillStyle = pool;
      ctx.fill();

      // Offset blot for depth
      const blot = ctx.createRadialGradient(
        cx + bodyR * 0.18,
        cy - bodyR * 0.1,
        0,
        cx + bodyR * 0.1,
        cy,
        bodyR * 0.55,
      );
      blot.addColorStop(0, "hsla(345 68% 36% / 0.42)");
      blot.addColorStop(0.55, "hsla(280 58% 38% / 0.22)");
      blot.addColorStop(1, "hsla(280 50% 40% / 0)");
      ctx.fillStyle = blot;
      ctx.beginPath();
      ctx.ellipse(cx + bodyR * 0.08, cy - bodyR * 0.04, bodyR * 0.48, bodyR * 0.36, spin * 0.4, 0, Math.PI * 2);
      ctx.fill();
    };

    const paintFrame = (t: number) => {
      ctx.globalCompositeOperation = "source-over";
      ctx.clearRect(0, 0, w, h);

      const { cx, cy, bodyR } = clusterGeom();
      const clusterSpin = t * 0.085;
      const breath = 1 + 0.025 * Math.sin(t * 0.38);

      paintWashBody(cx, cy, bodyR, clusterSpin, t);

      for (const p of particles) {
        const ang = p.phase + clusterSpin + t * p.spinBias + p.arm * 0.4;
        const envelope = lobeRadius(ang, bodyR, clusterSpin);
        const wob = Math.sin(t * p.wobbleSpeed + p.phase) * p.wobble;
        const rad = Math.min(envelope * 0.92, p.ring * envelope + wob) * breath;
        const x = cx + Math.cos(ang) * rad;
        const y = cy + Math.sin(ang) * rad * 0.86;
        drawBlot(ctx, x, y, p.rx, p.ry, ang * 0.3, p.hue, p.sat, p.light, p.alpha);
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

/** Soft ellipse blot — alpha falls off, hue stays (no white hot-spot). */
function drawBlot(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  rx: number,
  ry: number,
  rot: number,
  hue: number,
  sat: number,
  light: number,
  alpha: number,
) {
  ctx.save();
  ctx.translate(x, y);
  ctx.rotate(rot);
  const g = ctx.createRadialGradient(0, 0, 0, 0, 0, Math.max(rx, ry));
  g.addColorStop(0, `hsla(${hue} ${sat}% ${light}% / ${alpha})`);
  g.addColorStop(0.55, `hsla(${hue + 6} ${sat - 4}% ${light + 2}% / ${alpha * 0.55})`);
  g.addColorStop(1, `hsla(${hue} ${sat}% ${light}% / 0)`);
  ctx.fillStyle = g;
  ctx.beginPath();
  ctx.ellipse(0, 0, rx, ry, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();
}
