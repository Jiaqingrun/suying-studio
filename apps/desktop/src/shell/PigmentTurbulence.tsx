import { useEffect, useRef } from "react";

type Particle = {
  x: number;
  y: number;
  r: number;
  hue: number;
  sat: number;
  light: number;
  alpha: number;
  spin: number;
  orbit: number;
  phase: number;
  drift: number;
};

/**
 * Soft pigment turbulence behind the right-hand board.
 * Slow orbital swirl + gentle color bloom — decorative only.
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
      { hue: 290, sat: 58, light: 58 }, // plum
      { hue: 330, sat: 52, light: 62 }, // rose
      { hue: 198, sat: 55, light: 56 }, // mist blue
      { hue: 42, sat: 62, light: 58 }, // soft gold
      { hue: 168, sat: 48, light: 52 }, // teal wash
      { hue: 265, sat: 55, light: 64 }, // lilac
    ];

    const seed = (count: number) => {
      particles = Array.from({ length: count }, (_, i) => {
        const c = palette[i % palette.length];
        return {
          x: Math.random() * w,
          y: Math.random() * h,
          r: 18 + Math.random() * 54,
          hue: c.hue + (Math.random() * 18 - 9),
          sat: c.sat + (Math.random() * 10 - 5),
          light: c.light + (Math.random() * 8 - 4),
          alpha: 0.14 + Math.random() * 0.22,
          spin: (Math.random() * 0.22 + 0.06) * (Math.random() < 0.5 ? -1 : 1),
          orbit: 18 + Math.random() * 56,
          phase: Math.random() * Math.PI * 2,
          drift: 0.12 + Math.random() * 0.35,
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
      seed(Math.max(48, Math.floor((w * h) / 18000)));
    };

    const paintStatic = () => {
      ctx.clearRect(0, 0, w, h);
      for (const p of particles) {
        drawBlob(ctx, p.x, p.y, p.r, p.hue, p.sat, p.light, p.alpha * 0.85);
      }
    };

    const tick = (now: number) => {
      if (!running) return;
      const t = (now - t0) / 1000;
      ctx.globalCompositeOperation = "source-over";
      ctx.clearRect(0, 0, w, h);
      ctx.globalCompositeOperation = "lighter";
      const cx = w * 0.58;
      const cy = h * 0.46;
      const fieldAngle = t * 0.045;

      for (const p of particles) {
        const ox = Math.cos(fieldAngle + p.phase) * p.orbit;
        const oy = Math.sin(fieldAngle * 0.86 + p.phase * 1.3) * p.orbit * 0.72;
        const swirl = Math.sin(t * p.drift + p.phase) * 10;
        const px = p.x + ox + swirl;
        const py = p.y + oy + Math.cos(t * p.drift * 0.7 + p.phase) * 8;

        // Slow collective rotation around board center
        const dx = px - cx;
        const dy = py - cy;
        const ang = fieldAngle * 0.35 * p.spin;
        const rx = cx + dx * Math.cos(ang) - dy * Math.sin(ang);
        const ry = cy + dx * Math.sin(ang) + dy * Math.cos(ang);

        drawBlob(ctx, rx, ry, p.r * (0.92 + 0.08 * Math.sin(t + p.phase)), p.hue, p.sat, p.light, p.alpha);
      }

      raf = requestAnimationFrame(tick);
    };

    resize();
    const ro = new ResizeObserver(resize);
    if (canvas.parentElement) ro.observe(canvas.parentElement);

    ctx.globalCompositeOperation = "lighter";

    if (reduceMotion) {
      paintStatic();
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
  g.addColorStop(0.45, `hsla(${hue + 12} ${sat - 6}% ${light + 4}% / ${alpha * 0.45})`);
  g.addColorStop(1, `hsla(${hue} ${sat}% ${light}% / 0)`);
  ctx.fillStyle = g;
  ctx.beginPath();
  ctx.arc(x, y, r, 0, Math.PI * 2);
  ctx.fill();
}
