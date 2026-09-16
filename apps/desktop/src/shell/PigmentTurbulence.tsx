import { useEffect, useRef } from "react";

/**
 * Static silk nebula — baked once on resize (no per-frame RAF).
 * Perf: no live fbm hex mesh, no canvas filter blur each tick.
 */
export function PigmentTurbulence() {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d", { alpha: true });
    if (!ctx) return;

    let w = 0;
    let h = 0;
    let dpr = 1;
    let railW = 220;

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

    const paint = () => {
      readRail();
      dpr = Math.min(window.devicePixelRatio || 1, 1.5);
      w = Math.max(1, window.innerWidth);
      h = Math.max(1, window.innerHeight);
      canvas.width = Math.floor(w * dpr);
      canvas.height = Math.floor(h * dpr);
      canvas.style.width = `${w}px`;
      canvas.style.height = `${h}px`;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);

      const wellL = railW;
      const wellW = Math.max(320, w - wellL);
      const cx = wellL + wellW * 0.5;
      const cy = h * 0.44;
      const body = Math.min(wellW, h) * 0.38;
      const rx = body * 1.45;
      const ry = body * 1.15;

      // Soft pastel veil only — no hex mesh, no live noise
      const g = ctx.createRadialGradient(cx, cy, rx * 0.08, cx, cy, rx);
      g.addColorStop(0, "rgba(198, 168, 228, 0.22)");
      g.addColorStop(0.35, "rgba(168, 148, 218, 0.12)");
      g.addColorStop(0.65, "rgba(140, 170, 220, 0.06)");
      g.addColorStop(1, "rgba(235, 228, 239, 0)");
      ctx.fillStyle = g;
      ctx.beginPath();
      ctx.ellipse(cx, cy, rx, ry, 0, 0, Math.PI * 2);
      ctx.fill();

      const g2 = ctx.createRadialGradient(cx - rx * 0.12, cy - ry * 0.08, 0, cx, cy, rx * 0.55);
      g2.addColorStop(0, "rgba(210, 150, 200, 0.1)");
      g2.addColorStop(1, "rgba(210, 150, 200, 0)");
      ctx.fillStyle = g2;
      ctx.beginPath();
      ctx.ellipse(cx - rx * 0.08, cy - ry * 0.05, rx * 0.48, ry * 0.4, -0.2, 0, Math.PI * 2);
      ctx.fill();
    };

    paint();
    window.addEventListener("resize", paint);
    return () => window.removeEventListener("resize", paint);
  }, []);

  return <canvas ref={canvasRef} className="pigment-turbulence" aria-hidden="true" />;
}
