import { useEffect, useRef } from "react";

type Particle = {
  /** Normalized radius 0..1 within the cluster body */
  ring: number;
  phase: number;
  /** Differential spin vs the shared cluster turn */
  spinBias: number;
  squash: number;
  wobble: number;
  wobbleSpeed: number;
  r: number;
  hue: number;
  sat: number;
  light: number;
  alpha: number;
  layer: "core" | "body" | "halo";
};

/**
 * Fixed-viewport pigment nebula in the main board well.
 * Dense overlapping mass that turns as one body — does not scroll with page content.
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

    const palette = [
      { hue: 300, sat: 78, light: 56 },
      { hue: 330, sat: 74, light: 58 },
      { hue: 200, sat: 76, light: 54 },
      { hue: 45, sat: 82, light: 56 },
      { hue: 165, sat: 68, light: 50 },
      { hue: 270, sat: 72, light: 60 },
      { hue: 15, sat: 70, light: 58 },
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

    /** Cluster sits in the open main well (right of rail), viewport-fixed. */
    const clusterGeom = () => {
      const wellL = railW;
      const wellW = Math.max(320, w - wellL);
      const cx = wellL + wellW * 0.5;
      const cy = h * 0.44;
      // Volume of the nebula — large enough to read as a body
      const bodyR = Math.min(wellW, h) * 0.28;
      return { cx, cy, bodyR };
    };

    const seed = () => {
      const { bodyR } = clusterGeom();
      // Dense pack: many overlapping soft blobs → one volume
      const count = Math.max(96, Math.min(160, Math.floor((bodyR * bodyR) / 180)));
      particles = Array.from({ length: count }, (_, i) => {
        const c = palette[i % palette.length];
        // Cubic bias: most mass in the core, few outer filaments
        const ring = Math.pow(Math.random(), 2.1);
        const layer: Particle["layer"] = ring < 0.28 ? "core" : ring < 0.72 ? "body" : "halo";
        const sizeMul = layer === "core" ? 1.35 : layer === "body" ? 1.1 : 0.75;
        return {
          ring,
          phase: Math.random() * Math.PI * 2,
          spinBias: (Math.random() * 0.08 + 0.02) * (Math.random() < 0.42 ? -1 : 1),
          squash: 0.82 + Math.random() * 0.14,
          wobble: bodyR * (0.01 + Math.random() * 0.035),
          wobbleSpeed: 0.45 + Math.random() * 0.55,
          r: bodyR * (0.12 + Math.random() * 0.22) * sizeMul * (1.05 - ring * 0.35),
          hue: c.hue + (Math.random() * 18 - 9),
          sat: c.sat + (Math.random() * 10 - 5),
          light: c.light + (Math.random() * 8 - 4),
          alpha:
            layer === "core"
              ? 0.34 + Math.random() * 0.22
              : layer === "body"
                ? 0.26 + Math.random() * 0.18
                : 0.14 + Math.random() * 0.12,
          layer,
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

    const paintVolumeShell = (cx: number, cy: number, bodyR: number, t: number) => {
      const breath = 1 + 0.035 * Math.sin(t * 0.38);
      const R = bodyR * breath;

      // Deep pigment body — solid volume reading
      const deep = ctx.createRadialGradient(cx, cy, 0, cx, cy, R * 1.05);
      deep.addColorStop(0, "hsla(295 70% 58% / 0.42)");
      deep.addColorStop(0.22, "hsla(320 65% 56% / 0.32)");
      deep.addColorStop(0.48, "hsla(205 60% 54% / 0.2)");
      deep.addColorStop(0.72, "hsla(48 70% 52% / 0.1)");
      deep.addColorStop(1, "hsla(280 50% 55% / 0)");
      ctx.fillStyle = deep;
      ctx.beginPath();
      ctx.ellipse(cx, cy, R, R * 0.88, t * 0.04, 0, Math.PI * 2);
      ctx.fill();

      // Hot core
      const hot = ctx.createRadialGradient(cx - R * 0.08, cy - R * 0.06, 0, cx, cy, R * 0.42);
      hot.addColorStop(0, "hsla(40 85% 62% / 0.38)");
      hot.addColorStop(0.4, "hsla(330 75% 58% / 0.22)");
      hot.addColorStop(1, "hsla(300 60% 55% / 0)");
      ctx.fillStyle = hot;
      ctx.beginPath();
      ctx.arc(cx, cy, R * 0.42, 0, Math.PI * 2);
      ctx.fill();

      // Outer veil
      const veil = ctx.createRadialGradient(cx, cy, R * 0.55, cx, cy, R * 1.25);
      veil.addColorStop(0, "hsla(210 55% 60% / 0.08)");
      veil.addColorStop(1, "hsla(270 50% 60% / 0)");
      ctx.fillStyle = veil;
      ctx.beginPath();
      ctx.arc(cx, cy, R * 1.25, 0, Math.PI * 2);
      ctx.fill();
    };

    const paintFrame = (t: number) => {
      ctx.globalCompositeOperation = "source-over";
      ctx.clearRect(0, 0, w, h);
      ctx.globalCompositeOperation = "lighter";

      const { cx, cy, bodyR } = clusterGeom();
      // Shared rigid turn — whole mass rotates together
      const clusterSpin = t * 0.12;
      const breath = 1 + 0.03 * Math.sin(t * 0.4);

      paintVolumeShell(cx, cy, bodyR, t);

      for (const p of particles) {
        // Common spin + tiny differential = coherent turbulence, not scatter
        const ang = p.phase + clusterSpin + t * p.spinBias;
        const wob = Math.sin(t * p.wobbleSpeed + p.phase) * p.wobble;
        const rad = (p.ring * bodyR + wob) * breath;
        const x = cx + Math.cos(ang) * rad;
        const y = cy + Math.sin(ang) * rad * p.squash;
        const pulse = 0.9 + 0.1 * Math.sin(t * 0.85 + p.phase);
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
  g.addColorStop(0.35, `hsla(${hue + 8} ${sat - 2}% ${light + 3}% / ${alpha * 0.55})`);
  g.addColorStop(0.7, `hsla(${hue + 14} ${sat - 8}% ${light + 6}% / ${alpha * 0.2})`);
  g.addColorStop(1, `hsla(${hue} ${sat}% ${light}% / 0)`);
  ctx.fillStyle = g;
  ctx.beginPath();
  ctx.arc(x, y, r, 0, Math.PI * 2);
  ctx.fill();
}
