import {
  memo,
  useCallback,
  useEffect,
  useRef,
  type PointerEvent as ReactPointerEvent,
} from "react";
import {
  emojiGlyph,
  isEmojiSticker,
  isGeomMask,
  overlayPreviewSrc,
} from "./overlayPreview";
import type { Orientation, OrientationDefaults } from "./types";

type OverlayLayer = {
  id: string;
  source: string;
  x_pct: number;
  y_pct: number;
  scale: number;
  opacity: number;
  rotation_deg?: number;
  z: number;
  visible?: boolean;
};

type Props = {
  orientation: Orientation;
  od: OrientationDefaults;
  draft: Record<string, unknown>;
  onPatch: {
    (key: string, value: unknown): void;
    (partial: Record<string, unknown>): void;
  };
  selectedMaskId: string | null;
  onSelectMask: (id: string | null) => void;
  selectedStickerId?: string | null;
  onSelectSticker?: (id: string | null) => void;
};

function frameH(orientation: Orientation): number {
  return orientation === "portrait" ? 1920 : 1080;
}

function clampXPct(v: number): number {
  return Math.round(Math.max(8, Math.min(92, v)) * 10) / 10;
}

function alignFromX(x: number): "left" | "center" | "right" {
  if (x < 35) return "left";
  if (x > 65) return "right";
  return "center";
}

function resolveVerticalSubX(draft: Record<string, unknown>): number {
  const raw = draft.subtitle_x_pct;
  if (raw != null && Number(raw) !== 50) return clampXPct(Number(raw));
  const side = String(draft.subtitle_vertical_side || "left");
  return side === "right" ? 82 : 18;
}

type DragKind = "title" | "subtitle" | "mask" | "sticker";

type DragState = {
  kind: DragKind;
  id?: string;
  pointerId: number;
  titleTopPx: number;
  titleXPct: number;
  subBottomPx: number;
  subXPct: number;
  subYPct: number;
  maskX: number;
  maskY: number;
};

/**
 * Drag uses direct DOM writes (no React setState per move) to avoid jank from
 * re-painting stroked text every pointer event.
 */
function RuleCanvasImpl({
  orientation,
  od,
  draft,
  onPatch,
  selectedMaskId,
  onSelectMask,
  selectedStickerId = null,
  onSelectSticker,
}: Props) {
  const frameRef = useRef<HTMLDivElement | null>(null);
  const titleRef = useRef<HTMLElement | null>(null);
  const subRef = useRef<HTMLElement | null>(null);
  const titleGuideRef = useRef<HTMLDivElement | null>(null);
  const subGuideRef = useRef<HTMLDivElement | null>(null);
  const dragRef = useRef<DragState | null>(null);
  const pendingPtr = useRef<{ x: number; y: number } | null>(null);
  const rafRef = useRef(0);
  const odRef = useRef(od);
  const draftRef = useRef(draft);
  const onPatchRef = useRef(onPatch);
  const verticalRef = useRef(false);
  odRef.current = od;
  draftRef.current = draft;
  onPatchRef.current = onPatch;

  const h = frameH(orientation);
  const vertical = String(draft.subtitle_layout || "horizontal") === "vertical";
  verticalRef.current = vertical;

  const titleOn = draft.title_enabled !== false;
  const subOn = draft.subtitle_enabled !== false;

  const draftTitleTop = Number(draft.title_glyph_top_px ?? od.title_glyph_top_px);
  const draftSubBottom = Number(draft.subtitle_glyph_bottom_px ?? od.subtitle_glyph_bottom_px);
  const draftTitleX = Number(draft.title_x_pct ?? 50);
  const draftSubX = vertical ? resolveVerticalSubX(draft) : Number(draft.subtitle_x_pct ?? 50);
  const draftSubY = Number(draft.subtitle_y_pct ?? 50);
  const draftMasks = (Array.isArray(draft.mask_layers) ? draft.mask_layers : []) as OverlayLayer[];
  const draftStickers = (
    Array.isArray(draft.sticker_layers) ? draft.sticker_layers : []
  ) as OverlayLayer[];
  const titlePct = Math.min(48, (draftTitleTop / h) * 100);
  const subPct = Math.min(48, (draftSubBottom / h) * 100);

  const effect = String(draft.narration_text_effect || "none");
  const subLabel =
    effect === "karaoke" ? "卡拉OK预览" : effect === "marquee" ? "跑马灯预览" : "字幕预览";

  useEffect(() => {
    const frame = frameRef.current;
    if (!frame) return;
    frame.style.setProperty("--rule-title-color", String(draft.title_color || "#FFE600"));
    frame.style.setProperty("--rule-title-stroke", String(draft.title_stroke_color || "#000000"));
    frame.style.setProperty(
      "--rule-title-stroke-w",
      `${
        draft.title_stroke_enabled === false
          ? 0
          : Math.max(0, Number(draft.title_stroke_width || 0))
      }px`,
    );
    frame.style.setProperty("--rule-sub-color", String(draft.subtitle_color || "#FFFFFF"));
    frame.style.setProperty("--rule-sub-stroke", String(draft.subtitle_stroke_color || "#000000"));
    frame.style.setProperty(
      "--rule-sub-stroke-w",
      `${
        draft.subtitle_stroke_enabled === false
          ? 0
          : Math.max(0, Number(draft.subtitle_stroke_width || 0))
      }px`,
    );
  }, [
    draft.title_color,
    draft.title_stroke_color,
    draft.title_stroke_width,
    draft.title_stroke_enabled,
    draft.subtitle_color,
    draft.subtitle_stroke_color,
    draft.subtitle_stroke_width,
    draft.subtitle_stroke_enabled,
  ]);

  const clientToPct = useCallback((clientX: number, clientY: number) => {
    const el = frameRef.current;
    if (!el) return { x: 50, y: 50 };
    const rect = el.getBoundingClientRect();
    const x = ((clientX - rect.left) / Math.max(1, rect.width)) * 100;
    const y = ((clientY - rect.top) / Math.max(1, rect.height)) * 100;
    // Mask/sticker: free placement (wide soft bound only). Title/subtitle clamp separately.
    return {
      x: Math.max(-500, Math.min(500, x)),
      y: Math.max(-500, Math.min(500, y)),
    };
  }, []);

  const paintDrag = useCallback(() => {
    rafRef.current = 0;
    const drag = dragRef.current;
    const ptr = pendingPtr.current;
    if (!drag || !ptr) return;
    pendingPtr.current = null;
    const { x, y } = clientToPct(ptr.x, ptr.y);
    const odNow = odRef.current;
    const fh = frameH(orientation);

    if (drag.kind === "title") {
      const px = Math.round((y / 100) * fh);
      drag.titleTopPx = Math.max(
        odNow.title_glyph_top_px_min,
        Math.min(odNow.title_glyph_top_px_max, px),
      );
      drag.titleXPct = clampXPct(x);
      const topPct = Math.min(48, (drag.titleTopPx / fh) * 100);
      const titleEl = titleRef.current;
      if (titleEl) {
        titleEl.style.left = `${drag.titleXPct}%`;
        titleEl.style.top = `${topPct}%`;
      }
      if (titleGuideRef.current) titleGuideRef.current.style.top = `${topPct}%`;
    } else if (drag.kind === "subtitle" && !verticalRef.current) {
      const fromBottom = Math.round(((100 - y) / 100) * fh);
      drag.subBottomPx = Math.max(
        odNow.subtitle_glyph_bottom_px_min,
        Math.min(odNow.subtitle_glyph_bottom_px_max, fromBottom),
      );
      drag.subXPct = clampXPct(x);
      const bottomPct = Math.min(48, (drag.subBottomPx / fh) * 100);
      const subEl = subRef.current;
      if (subEl) {
        subEl.style.left = `${drag.subXPct}%`;
        subEl.style.bottom = `${bottomPct}%`;
        subEl.style.top = "auto";
        subEl.style.transform = "translateX(-50%)";
      }
      if (subGuideRef.current) subGuideRef.current.style.bottom = `${bottomPct}%`;
    } else if (drag.kind === "subtitle" && verticalRef.current) {
      drag.subXPct = clampXPct(x);
      drag.subYPct = clampXPct(y);
      const subEl = subRef.current;
      if (subEl) {
        subEl.style.left = `${drag.subXPct}%`;
        subEl.style.top = `${drag.subYPct}%`;
        subEl.style.right = "auto";
        subEl.style.bottom = "auto";
        subEl.style.transform = "translate(-50%, -50%)";
      }
    } else if ((drag.kind === "mask" || drag.kind === "sticker") && drag.id) {
      drag.maskX = Math.round(x * 10) / 10;
      drag.maskY = Math.round(y * 10) / 10;
      const handle = frameRef.current?.querySelector(
        `[data-overlay-id="${String(drag.id).replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"]`,
      ) as HTMLElement | null;
      if (handle) {
        handle.style.left = `${drag.maskX}%`;
        handle.style.top = `${drag.maskY}%`;
      }
    }
  }, [clientToPct, orientation]);

  const queuePointer = useCallback(
    (clientX: number, clientY: number) => {
      pendingPtr.current = { x: clientX, y: clientY };
      if (rafRef.current) return;
      rafRef.current = requestAnimationFrame(paintDrag);
    },
    [paintDrag],
  );

  const commitDrag = useCallback(() => {
    const drag = dragRef.current;
    if (!drag) return;
    dragRef.current = null;
    frameRef.current?.classList.remove("is-dragging");
    const patch = onPatchRef.current;
    if (drag.kind === "title") {
      patch({
        title_glyph_top_px: drag.titleTopPx,
        title_x_pct: drag.titleXPct,
        title_align: alignFromX(drag.titleXPct),
      });
    } else if (drag.kind === "subtitle") {
      if (verticalRef.current) {
        patch({
          subtitle_x_pct: drag.subXPct,
          subtitle_y_pct: drag.subYPct,
          subtitle_vertical_side: drag.subXPct < 50 ? "left" : "right",
        });
      } else {
        patch({
          subtitle_glyph_bottom_px: drag.subBottomPx,
          subtitle_x_pct: drag.subXPct,
          subtitle_align: alignFromX(drag.subXPct),
        });
      }
    } else if (drag.kind === "mask" && drag.id) {
      const masks = (Array.isArray(draftRef.current.mask_layers)
        ? draftRef.current.mask_layers
        : []) as OverlayLayer[];
      patch(
        "mask_layers",
        masks.map((m) =>
          m.id === drag.id ? { ...m, x_pct: drag.maskX, y_pct: drag.maskY } : m,
        ),
      );
    } else if (drag.kind === "sticker" && drag.id) {
      const stickers = (Array.isArray(draftRef.current.sticker_layers)
        ? draftRef.current.sticker_layers
        : []) as OverlayLayer[];
      patch(
        "sticker_layers",
        stickers.map((m) =>
          m.id === drag.id
            ? { ...m, x_pct: drag.maskX, y_pct: drag.maskY }
            : m,
        ),
      );
    }
  }, []);

  useEffect(() => {
    const onMove = (e: PointerEvent) => {
      if (!dragRef.current || e.pointerId !== dragRef.current.pointerId) return;
      queuePointer(e.clientX, e.clientY);
    };
    const onUp = (e: PointerEvent) => {
      if (!dragRef.current || e.pointerId !== dragRef.current.pointerId) return;
      if (rafRef.current) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = 0;
        paintDrag();
      }
      commitDrag();
    };
    window.addEventListener("pointermove", onMove, { passive: true });
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
    };
  }, [commitDrag, paintDrag, queuePointer]);

  const startDrag = (kind: DragKind, pointerId: number, id?: string) => {
    const d = draftRef.current;
    const odNow = odRef.current;
    const vert = String(d.subtitle_layout || "horizontal") === "vertical";
    dragRef.current = {
      kind,
      id,
      pointerId,
      titleTopPx: Number(d.title_glyph_top_px ?? odNow.title_glyph_top_px),
      titleXPct: Number(d.title_x_pct ?? 50),
      subBottomPx: Number(d.subtitle_glyph_bottom_px ?? odNow.subtitle_glyph_bottom_px),
      subXPct: vert ? resolveVerticalSubX(d) : Number(d.subtitle_x_pct ?? 50),
      subYPct: Number(d.subtitle_y_pct ?? 50),
      maskX: 50,
      maskY: 50,
    };
    if ((kind === "mask" || kind === "sticker") && id) {
      const layers = (
        Array.isArray(kind === "mask" ? d.mask_layers : d.sticker_layers)
          ? kind === "mask"
            ? d.mask_layers
            : d.sticker_layers
          : []
      ) as OverlayLayer[];
      const hit = layers.find((m) => m.id === id);
      if (hit) {
        dragRef.current.maskX = Number(hit.x_pct) || 50;
        dragRef.current.maskY = Number(hit.y_pct) || 50;
      }
    }
    frameRef.current?.classList.add("is-dragging");
  };

  return (
    <section className="rule-card rule-card--preview">
      <h3>画面布置</h3>
      <p className="hint rule-preview-hint">
        标题与字幕（含竖排）可<strong>拖动定位</strong>；右侧可开关标题/字幕。松手写入规则。
      </p>
      <div
        ref={frameRef}
        className={`rule-preview rule-preview--${orientation} rule-preview--canvas`}
      >
        {titleOn ? (
          <div
            ref={titleGuideRef}
            className="rule-safe-guide rule-safe-guide--title"
            style={{ top: `${titlePct}%` }}
          />
        ) : null}
        {!vertical && subOn ? (
          <div
            ref={subGuideRef}
            className="rule-safe-guide rule-safe-guide--sub"
            style={{ bottom: `${subPct}%` }}
          />
        ) : null}
        {titleOn ? (
          <strong
            ref={titleRef as React.RefObject<HTMLElement>}
            className="rule-drag-title"
            style={{
              left: `${draftTitleX}%`,
              top: `${titlePct}%`,
              cursor: "move",
            }}
            onPointerDown={(e: ReactPointerEvent) => {
              e.preventDefault();
              e.currentTarget.setPointerCapture(e.pointerId);
              startDrag("title", e.pointerId);
            }}
          >
            标题预览
          </strong>
        ) : null}
        {subOn ? (
          <span
            ref={subRef as React.RefObject<HTMLElement>}
            className={vertical ? "rule-drag-sub rule-drag-sub--vertical" : "rule-drag-sub"}
            style={
              vertical
                ? {
                    left: `${draftSubX}%`,
                    top: `${draftSubY}%`,
                    right: "auto",
                    bottom: "auto",
                    writingMode: "vertical-rl" as const,
                    cursor: "move",
                    transform: "translate(-50%, -50%)",
                    width: "auto",
                  }
                : {
                    left: `${draftSubX}%`,
                    bottom: `${subPct}%`,
                    cursor: "move",
                  }
            }
            onPointerDown={(e: ReactPointerEvent) => {
              e.preventDefault();
              e.currentTarget.setPointerCapture(e.pointerId);
              startDrag("subtitle", e.pointerId);
            }}
          >
            {subLabel}
          </span>
        ) : null}
        {draftMasks
          .filter((m) => m.visible !== false)
          .map((m) => {
            const src = String(m.source || "");
            const imgSrc = overlayPreviewSrc(src);
            const geom = isGeomMask(src);
            const label =
              src.replace(/^geom_/, "").replace(/^user:/, "").split("/").pop() || "层";
            const scale = Math.max(0.05, Math.min(12, Number(m.scale) || 1));
            const rot = Number(m.rotation_deg) || 0;
            return (
              <button
                key={m.id}
                type="button"
                data-overlay-id={m.id}
                className={
                  selectedMaskId === m.id
                    ? imgSrc
                      ? "rule-overlay-media is-active"
                      : "rule-mask-handle is-active"
                    : imgSrc
                      ? "rule-overlay-media"
                      : "rule-mask-handle"
                }
                style={
                  imgSrc
                    ? {
                        left: `${Number(m.x_pct) || 50}%`,
                        top: `${Number(m.y_pct) || 50}%`,
                        width: `${Math.max(2, 40 * scale)}%`,
                        opacity: Number(m.opacity) || 1,
                        transform: `translate(-50%, -50%) rotate(${rot}deg)`,
                      }
                    : {
                        left: `${Number(m.x_pct) || 50}%`,
                        top: `${Number(m.y_pct) || 50}%`,
                        opacity: Number(m.opacity) || 1,
                        transform: `translate(-50%, -50%) scale(${scale}) rotate(${rot}deg)`,
                      }
                }
                onPointerDown={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  e.currentTarget.setPointerCapture(e.pointerId);
                  onSelectMask(m.id);
                  startDrag("mask", e.pointerId, m.id);
                }}
                onWheel={(e) => {
                  if (!imgSrc && !geom) return;
                  e.preventDefault();
                  e.stopPropagation();
                  const cur = Math.max(0.05, Math.min(12, Number(m.scale) || 1));
                  const next = Math.max(
                    0.05,
                    Math.min(12, Math.round((cur + (e.deltaY > 0 ? -0.08 : 0.08)) * 100) / 100),
                  );
                  onPatch(
                    "mask_layers",
                    draftMasks.map((row) =>
                      row.id === m.id ? { ...row, scale: next } : row,
                    ),
                  );
                }}
                title={
                  geom
                    ? `几何蒙版 · ${label}（滚轮缩放）`
                    : "拖动定位 · 滚轮缩放 · 右侧可旋转"
                }
              >
                {imgSrc ? (
                  <img src={imgSrc} alt="" draggable={false} />
                ) : (
                  <>▣ {label}</>
                )}
              </button>
            );
          })}
        {draftStickers
          .filter((m) => m.visible !== false)
          .map((m) => {
            const src = String(m.source || "");
            const imgSrc = overlayPreviewSrc(src);
            const glyph = isEmojiSticker(src) ? emojiGlyph(src) : "✦";
            const scale = Math.max(0.05, Math.min(12, Number(m.scale) || 1));
            const rot = Number(m.rotation_deg) || 0;
            return (
              <button
                key={m.id}
                type="button"
                data-overlay-id={m.id}
                className={
                  selectedStickerId === m.id
                    ? imgSrc
                      ? "rule-overlay-media rule-overlay-media--sticker is-active"
                      : "rule-mask-handle rule-sticker-handle is-active"
                    : imgSrc
                      ? "rule-overlay-media rule-overlay-media--sticker"
                      : "rule-mask-handle rule-sticker-handle"
                }
                style={
                  imgSrc
                    ? {
                        left: `${Number(m.x_pct) || 50}%`,
                        top: `${Number(m.y_pct) || 42}%`,
                        width: `${Math.max(2, 28 * scale)}%`,
                        opacity: Number(m.opacity) || 1,
                        transform: `translate(-50%, -50%) rotate(${rot}deg)`,
                      }
                    : {
                        left: `${Number(m.x_pct) || 50}%`,
                        top: `${Number(m.y_pct) || 42}%`,
                        opacity: Number(m.opacity) || 1,
                        transform: `translate(-50%, -50%) scale(${scale}) rotate(${rot}deg)`,
                      }
                }
                onPointerDown={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  e.currentTarget.setPointerCapture(e.pointerId);
                  onSelectSticker?.(m.id);
                  startDrag("sticker", e.pointerId, m.id);
                }}
                onWheel={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  const cur = Math.max(0.05, Math.min(12, Number(m.scale) || 1));
                  const next = Math.max(
                    0.05,
                    Math.min(12, Math.round((cur + (e.deltaY > 0 ? -0.08 : 0.08)) * 100) / 100),
                  );
                  onPatch(
                    "sticker_layers",
                    draftStickers.map((row) =>
                      row.id === m.id ? { ...row, scale: next } : row,
                    ),
                  );
                }}
                title="拖动定位 · 滚轮缩放 · 右侧可旋转"
              >
                {imgSrc ? <img src={imgSrc} alt="" draggable={false} /> : glyph}
              </button>
            );
          })}
      </div>
    </section>
  );
}

export const RuleCanvas = memo(RuleCanvasImpl);
