import type { LangCatalogItem, NotifyFn } from "../pages/pageTypes";
import { VoiceTtsPanel } from "../pages/SettingsPage";
import { LangCombobox } from "../shell/LangCombobox";
import { contentCategoryLabel, templatePreferenceLabel } from "../sceneTourLabels";
import {
  asColorHex,
  asList,
  packagingOpenCount,
  parseList,
  resolveOrientationDefaults,
} from "./defaults";
import {
  STICKER_EMOJI_PRESETS,
  TRANSITION_FEATURED,
  overlayBasename,
  transitionLabel,
} from "./fxCatalog";
import { pickOverlayImage, toUserSource } from "./pickOverlay";
import { RuleCanvas } from "./RuleCanvas";
import type { Orientation, RuleRow } from "./types";
import { useEffect, useMemo, useState, type ChangeEvent } from "react";

type LabApi = ReturnType<typeof import("./useRuleLabState").useRuleLabState>;

function clampNum(n: number, lo: number, hi: number): number {
  if (!Number.isFinite(n)) return lo;
  return Math.max(lo, Math.min(hi, n));
}

/** Slider + number input for overlay transform (number commits on Enter / blur). */
function OverlayTransformControls({
  scale,
  rotationDeg,
  opacity,
  onScale,
  onRotation,
  onOpacity,
}: {
  scale: number;
  rotationDeg: number;
  opacity: number;
  onScale: (v: number) => void;
  onRotation: (v: number) => void;
  onOpacity: (v: number) => void;
}) {
  const [scaleText, setScaleText] = useState(String(Number(scale.toFixed(2))));
  const [rotText, setRotText] = useState(String(Math.round(rotationDeg)));
  const [opText, setOpText] = useState(String(Number(opacity.toFixed(2))));

  useEffect(() => {
    setScaleText(String(Number(scale.toFixed(2))));
  }, [scale]);
  useEffect(() => {
    setRotText(String(Math.round(rotationDeg)));
  }, [rotationDeg]);
  useEffect(() => {
    setOpText(String(Number(opacity.toFixed(2))));
  }, [opacity]);

  const commitScale = () => {
    const next = clampNum(Number(scaleText), 0.05, 12);
    setScaleText(String(Number(next.toFixed(2))));
    onScale(next);
  };
  const commitRot = () => {
    const next = clampNum(Number(rotText), -180, 180);
    setRotText(String(Math.round(next)));
    onRotation(next);
  };
  const commitOp = () => {
    const next = clampNum(Number(opText), 0.05, 1);
    setOpText(String(Number(next.toFixed(2))));
    onOpacity(next);
  };

  const onNumKey = (e: { key: string; preventDefault: () => void; currentTarget: HTMLInputElement }, commit: () => void) => {
    if (e.key === "Enter") {
      e.preventDefault();
      commit();
      e.currentTarget.blur();
    }
  };

  return (
    <div className="rule-overlay-transform">
      <div className="rule-overlay-tf-row">
        <span className="rule-overlay-tf-label">缩放</span>
        <input
          type="range"
          min={0.05}
          max={8}
          step={0.01}
          value={clampNum(scale, 0.05, 8)}
          onChange={(e: ChangeEvent<HTMLInputElement>) => onScale(Number(e.target.value))}
          aria-label="缩放滑条"
        />
        <input
          type="number"
          className="rule-overlay-tf-num"
          min={0.05}
          max={12}
          step={0.05}
          value={scaleText}
          onChange={(e) => setScaleText(e.target.value)}
          onBlur={commitScale}
          onKeyDown={(e) => onNumKey(e, commitScale)}
          aria-label="缩放数值"
        />
        <span className="rule-overlay-tf-unit">×</span>
      </div>
      <div className="rule-overlay-tf-row">
        <span className="rule-overlay-tf-label">旋转</span>
        <input
          type="range"
          min={-180}
          max={180}
          step={1}
          value={clampNum(rotationDeg, -180, 180)}
          onChange={(e: ChangeEvent<HTMLInputElement>) => onRotation(Number(e.target.value))}
          aria-label="旋转滑条"
        />
        <input
          type="number"
          className="rule-overlay-tf-num"
          min={-180}
          max={180}
          step={1}
          value={rotText}
          onChange={(e) => setRotText(e.target.value)}
          onBlur={commitRot}
          onKeyDown={(e) => onNumKey(e, commitRot)}
          aria-label="旋转数值"
        />
        <span className="rule-overlay-tf-unit">°</span>
      </div>
      <div className="rule-overlay-tf-row">
        <span className="rule-overlay-tf-label">不透明度</span>
        <input
          type="range"
          min={0.05}
          max={1}
          step={0.01}
          value={clampNum(opacity, 0.05, 1)}
          onChange={(e: ChangeEvent<HTMLInputElement>) => onOpacity(Number(e.target.value))}
          aria-label="不透明度滑条"
        />
        <input
          type="number"
          className="rule-overlay-tf-num"
          min={0.05}
          max={1}
          step={0.01}
          value={opText}
          onChange={(e) => setOpText(e.target.value)}
          onBlur={commitOp}
          onKeyDown={(e) => onNumKey(e, commitOp)}
          aria-label="不透明度数值"
        />
      </div>
    </div>
  );
}

type Props = {
  lab: LabApi;
  customerName?: string;
  orientation: Orientation;
  languages: LangCatalogItem[];
  notify: NotifyFn;
  activeCustomerId?: number | null;
  pickFile: () => Promise<string | null>;
  onGoTasks?: () => void;
  runDryRunWithRule?: (ruleId: number | null) => void;
};

export function RuleStatusHeader({
  lab,
  customerName,
  orientation,
}: {
  lab: LabApi;
  customerName?: string;
  orientation: Orientation;
}) {
  const active = lab.activeRow;
  const catHint =
    lab.contentCategory === "premium"
      ? "精品样式：可开安全包装、转场、多层蒙版与旁白呈现；只作用于本类别任务。"
      : lab.contentCategory === "scene_tour"
        ? "跟镜精品：旁白跟随画面讲解，不走词池金句；须先完成场景分类。"
        : "日常日更：建议保持安全包装关闭；仅影响新任务。";
  return (
    <div className="rule-lab-intro">
      <label>
        内容类别
        <select
          value={lab.contentCategory}
          onChange={(e) => lab.switchContentCategory(e.target.value)}
        >
          {lab.categories.map((category) => (
            <option key={category} value={category}>
              {contentCategoryLabel(category)}
            </option>
          ))}
        </select>
      </label>
      <div className="rule-lab-category-actions">
        <button
          type="button"
          className="ghost"
          disabled={Boolean(lab.busy)}
          onClick={() => void lab.cloneToPremium()}
        >
          {lab.busy === "premium" ? "克隆中…" : "克隆为精品稿"}
        </button>
        <span className="hint">从当前选中/启用规则复制到精品样式，仅草稿不自动启用</span>
      </div>
      <p>先选择推荐效果，再按需微调。保存后只影响新任务，正在制作的视频不会改变。</p>
      <p className="muted">{catHint}</p>
      <p className="muted">
        当前客户：{customerName || "—"} · 类别：{contentCategoryLabel(lab.contentCategory)} ·{" "}
        {orientation === "landscape" ? "横屏" : "竖屏"}
      </p>
      {active ? (
        <p className="muted">
          当前正在使用：{active.name} r{active.revision}
          {String(lab.draft.tts_provider) === "clone"
            ? ` · 音色 克隆·${String(lab.draft.voice_pack || "")}`
            : ` · 音色 Edge·${String(lab.draft.tts_voice || "")}`}
        </p>
      ) : (
        <p className="muted">尚未启用默认规则；任务仍可按主题/分类运行。</p>
      )}
      <p className="muted">
        {lab.rotationPolicy
          ? `规则轮换已开：每个任务抽一套已保存规则并冻结（池内 ${lab.rotationPoolCount} 条；草稿不参与）`
          : "规则轮换已关：新任务使用当前类别启用规则"}
      </p>
    </div>
  );
}

export function RuleMainEditor({
  lab,
  orientation,
  languages,
  notify,
  activeCustomerId,
  pickFile,
  onGoTasks,
  runDryRunWithRule,
}: Props) {
  const od = resolveOrientationDefaults(lab.schema, orientation);
  const packCount = packagingOpenCount(lab.draft);
  const dryRunId = lab.selected?.id ?? lab.activeId;
  const [selectedMaskId, setSelectedMaskId] = useState<string | null>(null);
  const [selectedStickerId, setSelectedStickerId] = useState<string | null>(null);
  const fx = lab.schema?.fx_assets;
  const xfadeList = useMemo(() => {
    const raw = (fx?.xfade_transitions as string[] | undefined) || [];
    return raw.length ? raw : ["fade", "dissolve", "wipeleft", "wiperight", "slideup", "slidedown"];
  }, [fx]);
  const featuredTransitions = useMemo(() => {
    const set = new Set(xfadeList);
    return TRANSITION_FEATURED.filter((id) => id === "none" || set.has(id));
  }, [xfadeList]);
  const fontOptions = useMemo(() => {
    const raw =
      (fx?.fonts as Array<{ id: string; label: string; use_hint?: string | null; available?: boolean }> | undefined) ||
      [];
    return raw.length
      ? raw
      : [{ id: "default", label: "系统默认", use_hint: "通用兜底", available: true }];
  }, [fx]);
  const builtinMasks = useMemo(() => {
    const raw = (fx?.builtin_masks as Array<{ id: string; label: string }> | undefined) || [];
    return raw.length
      ? raw
      : [
          { id: "geom_vignette", label: "暗角" },
          { id: "geom_bottom_gradient", label: "底部渐暗" },
          { id: "geom_top_gradient", label: "顶部渐暗" },
        ];
  }, [fx]);
  const maskLayers = (Array.isArray(lab.draft.mask_layers) ? lab.draft.mask_layers : []) as Array<
    Record<string, unknown>
  >;
  const stickerLayers = (
    Array.isArray(lab.draft.sticker_layers) ? lab.draft.sticker_layers : []
  ) as Array<Record<string, unknown>>;

  const addMaskLayer = (source: string, yPct = 55) => {
    if (maskLayers.length >= 8) {
      notify("蒙版最多 8 层", "warn");
      return;
    }
    const id = `mask-${Date.now()}-${maskLayers.length}`;
    lab.patchDraft("mask_layers", [
      ...maskLayers,
      {
        id,
        source,
        x_pct: 50,
        y_pct: yPct,
        scale: 1,
        opacity: 0.85,
        rotation_deg: 0,
        z: maskLayers.length,
        visible: true,
      },
    ]);
    setSelectedMaskId(id);
    setSelectedStickerId(null);
  };

  const addStickerLayer = (source: string) => {
    if (stickerLayers.length >= 8) {
      notify("贴图最多 8 层", "warn");
      return;
    }
    const id = `sticker-${Date.now()}-${stickerLayers.length}`;
    lab.patchDraft("sticker_layers", [
      ...stickerLayers,
      {
        id,
        source,
        x_pct: 78,
        y_pct: 42,
        scale: 1,
        opacity: 1,
        rotation_deg: 0,
        z: stickerLayers.length,
        visible: true,
      },
    ]);
    setSelectedStickerId(id);
    setSelectedMaskId(null);
  };

  return (
    <>
      <div className="rule-lab-quick-grid">
        <RuleCanvas
          orientation={orientation}
          od={od}
          draft={lab.draft}
          onPatch={lab.patchDraft}
          selectedMaskId={selectedMaskId}
          onSelectMask={(id) => {
            setSelectedMaskId(id);
            if (id) setSelectedStickerId(null);
          }}
          selectedStickerId={selectedStickerId}
          onSelectSticker={(id) => {
            setSelectedStickerId(id);
            if (id) setSelectedMaskId(null);
          }}
        />

        <div className="rule-lab-side-stack">
          <section className="rule-card rule-card--pace">
            <h3>成片气质</h3>
            <p className="hint">一键设定剪辑节奏与旁白语气。</p>
            <div className="rule-pace-grid">
              {(
                [
                  ["清晰日常", "normal", "plain", "节奏正常 · 旁白朴实"],
                  ["轻快展示", "fast", "energetic", "节奏偏快 · 旁白有劲"],
                  ["稳重介绍", "slow", "professional", "节奏偏慢 · 旁白专业"],
                ] as const
              ).map(([label, pace, tone, desc]) => {
                const active =
                  String(lab.draft.pace || "normal") === pace &&
                  String(lab.draft.narration_tone || "plain") === tone;
                return (
                  <button
                    key={label}
                    type="button"
                    className={active ? "rule-pace-btn is-active" : "rule-pace-btn"}
                    onClick={() => {
                      lab.setDraft((previous) => ({
                        ...previous,
                        pace,
                        narration_tone: tone,
                        orientation,
                      }));
                      lab.setDirty(true);
                    }}
                  >
                    <strong>{label}</strong>
                    <span>{desc}</span>
                  </button>
                );
              })}
            </div>
          </section>

          <section className="rule-card rule-card--visual">
            <h3>视觉层与字幕动效</h3>
            <p className="hint">字段默认关 · 精品可开。添加后可在左侧大画布拖动手柄。</p>

            <h4>字幕动效</h4>
            <div className="rule-fx-chip-row" role="group" aria-label="字幕动效">
              {(
                [
                  ["none", "无"],
                  ["karaoke", "卡拉OK"],
                  ["marquee", "跑马灯"],
                ] as const
              ).map(([id, label]) => {
                const on = String(lab.draft.narration_text_effect || "none") === id;
                return (
                  <button
                    key={id}
                    type="button"
                    className={on ? "rule-fx-chip is-active" : "rule-fx-chip"}
                    onClick={() => lab.patchDraft("narration_text_effect", id)}
                  >
                    {label}
                  </button>
                );
              })}
            </div>
            <div className="grid2 rule-visual-mini">
              <label>
                排版
                <select
                  value={String(lab.draft.subtitle_layout || "horizontal")}
                  onChange={(e) => {
                    const layout = e.target.value;
                    if (layout === "vertical") {
                      const side =
                        String(lab.draft.subtitle_vertical_side || "left") === "right"
                          ? "right"
                          : "left";
                      lab.setDraft((prev) => ({
                        ...prev,
                        subtitle_layout: layout,
                        subtitle_vertical_side: side,
                        subtitle_x_pct:
                          Number(prev.subtitle_x_pct) === 50 || prev.subtitle_x_pct == null
                            ? side === "right"
                              ? 82
                              : 18
                            : prev.subtitle_x_pct,
                        subtitle_y_pct: prev.subtitle_y_pct ?? 50,
                      }));
                      lab.setDirty(true);
                    } else {
                      lab.patchDraft("subtitle_layout", layout);
                    }
                  }}
                >
                  <option value="horizontal">横排</option>
                  <option value="vertical">竖排</option>
                </select>
              </label>
              <label>
                转场时长(秒)
                <input
                  type="number"
                  min={0.05}
                  max={0.8}
                  step={0.05}
                  value={Number(lab.draft.clip_transition_duration_sec ?? 0.4)}
                  onChange={(e) =>
                    lab.patchDraft("clip_transition_duration_sec", Number(e.target.value))
                  }
                  disabled={String(lab.draft.clip_transition || "none") === "none"}
                />
              </label>
            </div>

            <h4>镜头转场库</h4>
            <p className="hint">
              受控列表（FFmpeg xfade），不可导入第三方特效包。点选常用项，或从完整列表指定。
            </p>
            <div className="rule-fx-chip-row" role="group" aria-label="常用转场">
              {featuredTransitions.map((id) => {
                const on = String(lab.draft.clip_transition || "none") === id;
                return (
                  <button
                    key={id}
                    type="button"
                    className={on ? "rule-fx-chip is-active" : "rule-fx-chip"}
                    onClick={() => lab.patchDraft("clip_transition", id)}
                  >
                    {transitionLabel(id)}
                  </button>
                );
              })}
            </div>
            <label className="rule-fx-full-select">
              完整列表
              <select
                value={String(lab.draft.clip_transition || "none")}
                onChange={(e) => lab.patchDraft("clip_transition", e.target.value)}
              >
                <option value="none">{transitionLabel("none")}</option>
                {xfadeList.map((name) => (
                  <option key={name} value={name}>
                    {transitionLabel(name)}
                  </option>
                ))}
              </select>
            </label>

            <h4>蒙版图层 ▣</h4>
            <p className="hint">压暗/渐变衬字，或导入自己的 PNG 蒙版；最多 8 层。画布可自由拖放（含画幅外），滚轮缩放；右侧可输入旋转/缩放/不透明度。</p>
            <div className="rule-mask-add">
              {builtinMasks.map((m) => (
                <button
                  key={m.id}
                  type="button"
                  className="ghost rule-mask-add-btn"
                  disabled={maskLayers.length >= 8}
                  onClick={() => addMaskLayer(m.id)}
                >
                  + {m.label}
                </button>
              ))}
              <button
                type="button"
                className="ghost rule-mask-add-btn"
                disabled={maskLayers.length >= 8}
                onClick={() => {
                  void pickOverlayImage("导入蒙版图片").then((path) => {
                    if (!path) return;
                    addMaskLayer(toUserSource(path), 50);
                    notify("已导入蒙版图", "ok");
                  });
                }}
              >
                + 导入蒙版图
              </button>
            </div>
            <ul className="rule-mask-list">
              {maskLayers.length === 0 ? (
                <li className="hint">暂无蒙版 — 点上方添加或导入后，在画布拖 ▣</li>
              ) : null}
              {maskLayers.map((m, idx) => (
                <li key={String(m.id)}>
                  <button
                    type="button"
                    className={selectedMaskId === m.id ? "is-active" : undefined}
                    onClick={() => {
                      setSelectedMaskId(String(m.id));
                      setSelectedStickerId(null);
                    }}
                  >
                    ▣ {overlayBasename(String(m.source))} · z{Number(m.z ?? idx)}
                  </button>
                  <button
                    type="button"
                    className="ghost"
                    onClick={() => {
                      lab.patchDraft(
                        "mask_layers",
                        maskLayers.filter((row) => row.id !== m.id),
                      );
                      if (selectedMaskId === m.id) setSelectedMaskId(null);
                    }}
                  >
                    删除
                  </button>
                </li>
              ))}
            </ul>
            {selectedMaskId ? (
              <OverlayTransformControls
                scale={Number(maskLayers.find((m) => m.id === selectedMaskId)?.scale ?? 1)}
                rotationDeg={Number(
                  maskLayers.find((m) => m.id === selectedMaskId)?.rotation_deg ?? 0,
                )}
                opacity={Number(
                  maskLayers.find((m) => m.id === selectedMaskId)?.opacity ?? 0.85,
                )}
                onScale={(scale) =>
                  lab.patchDraft(
                    "mask_layers",
                    maskLayers.map((m) => (m.id === selectedMaskId ? { ...m, scale } : m)),
                  )
                }
                onRotation={(rotation_deg) =>
                  lab.patchDraft(
                    "mask_layers",
                    maskLayers.map((m) =>
                      m.id === selectedMaskId ? { ...m, rotation_deg } : m,
                    ),
                  )
                }
                onOpacity={(opacity) =>
                  lab.patchDraft(
                    "mask_layers",
                    maskLayers.map((m) => (m.id === selectedMaskId ? { ...m, opacity } : m)),
                  )
                }
              />
            ) : null}

            <h4>装饰贴图 ✦</h4>
            <p className="hint">表情或自有 PNG；可自由摆放（含标题区外/画幅外）；最多 8 层，画布可拖 ✦。</p>
            <div className="rule-mask-add">
              {STICKER_EMOJI_PRESETS.map((glyph) => (
                <button
                  key={glyph}
                  type="button"
                  className="ghost rule-mask-add-btn"
                  disabled={stickerLayers.length >= 8}
                  onClick={() => addStickerLayer(`twemoji:${glyph}`)}
                >
                  + {glyph}
                </button>
              ))}
              <button
                type="button"
                className="ghost rule-mask-add-btn"
                disabled={stickerLayers.length >= 8}
                onClick={() => {
                  void pickOverlayImage("导入贴图").then((path) => {
                    if (!path) return;
                    addStickerLayer(toUserSource(path));
                    notify("已导入贴图", "ok");
                  });
                }}
              >
                + 导入贴图
              </button>
            </div>
            <ul className="rule-mask-list">
              {stickerLayers.length === 0 ? (
                <li className="hint">暂无贴图 — 选表情或导入图片</li>
              ) : null}
              {stickerLayers.map((m, idx) => (
                <li key={String(m.id)}>
                  <button
                    type="button"
                    className={selectedStickerId === m.id ? "is-active" : undefined}
                    onClick={() => {
                      setSelectedStickerId(String(m.id));
                      setSelectedMaskId(null);
                    }}
                  >
                    ✦ {overlayBasename(String(m.source))} · z{Number(m.z ?? idx)}
                  </button>
                  <button
                    type="button"
                    className="ghost"
                    onClick={() => {
                      lab.patchDraft(
                        "sticker_layers",
                        stickerLayers.filter((row) => row.id !== m.id),
                      );
                      if (selectedStickerId === m.id) setSelectedStickerId(null);
                    }}
                  >
                    删除
                  </button>
                </li>
              ))}
            </ul>
            {selectedStickerId ? (
              <OverlayTransformControls
                scale={Number(stickerLayers.find((m) => m.id === selectedStickerId)?.scale ?? 1)}
                rotationDeg={Number(
                  stickerLayers.find((m) => m.id === selectedStickerId)?.rotation_deg ?? 0,
                )}
                opacity={Number(
                  stickerLayers.find((m) => m.id === selectedStickerId)?.opacity ?? 1,
                )}
                onScale={(scale) =>
                  lab.patchDraft(
                    "sticker_layers",
                    stickerLayers.map((m) =>
                      m.id === selectedStickerId ? { ...m, scale } : m,
                    ),
                  )
                }
                onRotation={(rotation_deg) =>
                  lab.patchDraft(
                    "sticker_layers",
                    stickerLayers.map((m) =>
                      m.id === selectedStickerId ? { ...m, rotation_deg } : m,
                    ),
                  )
                }
                onOpacity={(opacity) =>
                  lab.patchDraft(
                    "sticker_layers",
                    stickerLayers.map((m) =>
                      m.id === selectedStickerId ? { ...m, opacity } : m,
                    ),
                  )
                }
              />
            ) : null}
          </section>
        </div>

        <section className="rule-card rule-card--text rule-card--span-2">
          <h3>标题与字幕</h3>
          <div className="rule-text-split">
            <div className="rule-text-block">
              <h4>标题</h4>
              <label className="rule-toggle-row">
                <input
                  type="checkbox"
                  checked={lab.draft.title_enabled !== false}
                  onChange={(e) => lab.patchDraft("title_enabled", e.target.checked)}
                />
                显示标题
              </label>
              <label className="rule-toggle-row">
                <input
                  type="checkbox"
                  checked={lab.draft.title_stroke_enabled !== false}
                  onChange={(e) => lab.patchDraft("title_stroke_enabled", e.target.checked)}
                />
                标题描边
              </label>
              <div className="grid3">
                <label>
                  颜色
                  <input
                    type="color"
                    value={asColorHex(lab.draft.title_color, "#FFE600")}
                    onChange={(e) => lab.patchDraft("title_color", e.target.value)}
                  />
                </label>
                <label>
                  描边
                  <input
                    type="color"
                    value={asColorHex(lab.draft.title_stroke_color, "#000000")}
                    onChange={(e) => lab.patchDraft("title_stroke_color", e.target.value)}
                    disabled={lab.draft.title_stroke_enabled === false}
                  />
                </label>
                <label>
                  描边宽度
                  <input
                    type="number"
                    min={od.title_stroke_width_min}
                    max={od.title_stroke_width_max}
                    value={Number(lab.draft.title_stroke_width ?? od.title_stroke_width)}
                    onChange={(e) => lab.patchDraft("title_stroke_width", Number(e.target.value))}
                    disabled={lab.draft.title_stroke_enabled === false}
                  />
                </label>
                <label>
                  字号
                  <input
                    type="number"
                    min={od.title_font_size_min}
                    max={od.title_font_size_max}
                    value={Number(lab.draft.title_font_size ?? od.title_font_size)}
                    onChange={(e) => lab.patchDraft("title_font_size", Number(e.target.value))}
                  />
                </label>
                <label>
                  距顶部
                  <input
                    type="number"
                    min={od.title_glyph_top_px_min}
                    max={od.title_glyph_top_px_max}
                    value={Number(lab.draft.title_glyph_top_px ?? od.title_glyph_top_px)}
                    onChange={(e) => lab.patchDraft("title_glyph_top_px", Number(e.target.value))}
                  />
                </label>
                <label>
                  水平位置%
                  <input
                    type="number"
                    min={8}
                    max={92}
                    step={0.5}
                    value={Number(lab.draft.title_x_pct ?? 50)}
                    onChange={(e) => {
                      const x = Math.max(8, Math.min(92, Number(e.target.value)));
                      lab.setDraft((prev) => ({
                        ...prev,
                        title_x_pct: x,
                        title_align: x < 35 ? "left" : x > 65 ? "right" : "center",
                      }));
                      lab.setDirty(true);
                    }}
                  />
                </label>
                <label>
                  对齐
                  <select
                    value={String(lab.draft.title_align || "center")}
                    onChange={(e) => {
                      const align = e.target.value;
                      const x = align === "left" ? 18 : align === "right" ? 82 : 50;
                      lab.setDraft((prev) => ({
                        ...prev,
                        title_align: align,
                        title_x_pct: x,
                      }));
                      lab.setDirty(true);
                    }}
                  >
                    <option value="left">左</option>
                    <option value="center">中</option>
                    <option value="right">右</option>
                  </select>
                </label>
                <label>
                  出现效果
                  <select
                    value={String(lab.draft.title_effect || "none")}
                    onChange={(e) => lab.patchDraft("title_effect", e.target.value)}
                  >
                    <option value="none">无</option>
                    <option value="fade">淡入</option>
                  </select>
                </label>
                <label>
                  标题字体
                  <select
                    value={String(lab.draft.title_font_family || "default")}
                    onChange={(e) => lab.patchDraft("title_font_family", e.target.value)}
                  >
                    {fontOptions.map((f) => (
                      <option key={f.id} value={f.id} disabled={f.available === false}>
                        {f.label}
                        {f.use_hint ? ` · ${f.use_hint}` : ""}
                        {f.available === false ? "（未下载）" : ""}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
            </div>
            <div className="rule-text-block">
              <h4>字幕</h4>
              <label className="rule-toggle-row">
                <input
                  type="checkbox"
                  checked={lab.draft.subtitle_enabled !== false}
                  onChange={(e) => lab.patchDraft("subtitle_enabled", e.target.checked)}
                />
                显示字幕
              </label>
              <label className="rule-toggle-row">
                <input
                  type="checkbox"
                  checked={lab.draft.subtitle_stroke_enabled !== false}
                  onChange={(e) => lab.patchDraft("subtitle_stroke_enabled", e.target.checked)}
                />
                字幕描边
              </label>
              <div className="grid3">
                <label>
                  颜色
                  <input
                    type="color"
                    value={asColorHex(lab.draft.subtitle_color, "#FFFFFF")}
                    onChange={(e) => lab.patchDraft("subtitle_color", e.target.value)}
                  />
                </label>
                <label>
                  描边
                  <input
                    type="color"
                    value={asColorHex(lab.draft.subtitle_stroke_color, "#000000")}
                    onChange={(e) => lab.patchDraft("subtitle_stroke_color", e.target.value)}
                    disabled={lab.draft.subtitle_stroke_enabled === false}
                  />
                </label>
                <label>
                  描边宽度
                  <input
                    type="number"
                    min={od.subtitle_stroke_width_min}
                    max={od.subtitle_stroke_width_max}
                    value={Number(lab.draft.subtitle_stroke_width ?? od.subtitle_stroke_width)}
                    onChange={(e) => lab.patchDraft("subtitle_stroke_width", Number(e.target.value))}
                    disabled={lab.draft.subtitle_stroke_enabled === false}
                  />
                </label>
                <label>
                  字号
                  <input
                    type="number"
                    min={od.subtitle_font_size_min}
                    max={od.subtitle_font_size_max}
                    value={Number(lab.draft.subtitle_font_size ?? od.subtitle_font_size)}
                    onChange={(e) => lab.patchDraft("subtitle_font_size", Number(e.target.value))}
                  />
                </label>
                <label>
                  距底部
                  <input
                    type="number"
                    min={od.subtitle_glyph_bottom_px_min}
                    max={od.subtitle_glyph_bottom_px_max}
                    value={Number(lab.draft.subtitle_glyph_bottom_px ?? od.subtitle_glyph_bottom_px)}
                    onChange={(e) =>
                      lab.patchDraft("subtitle_glyph_bottom_px", Number(e.target.value))
                    }
                  />
                </label>
                <label>
                  水平位置%
                  <input
                    type="number"
                    min={8}
                    max={92}
                    step={0.5}
                    value={Number(lab.draft.subtitle_x_pct ?? 50)}
                    onChange={(e) => {
                      const x = Math.max(8, Math.min(92, Number(e.target.value)));
                      const vertical =
                        String(lab.draft.subtitle_layout || "horizontal") === "vertical";
                      lab.setDraft((prev) => ({
                        ...prev,
                        subtitle_x_pct: x,
                        subtitle_align: x < 35 ? "left" : x > 65 ? "right" : "center",
                        ...(vertical
                          ? { subtitle_vertical_side: x < 50 ? "left" : "right" }
                          : {}),
                      }));
                      lab.setDirty(true);
                    }}
                  />
                </label>
                <label>
                  竖排垂直%
                  <input
                    type="number"
                    min={8}
                    max={92}
                    step={0.5}
                    value={Number(lab.draft.subtitle_y_pct ?? 50)}
                    onChange={(e) =>
                      lab.patchDraft(
                        "subtitle_y_pct",
                        Math.max(8, Math.min(92, Number(e.target.value))),
                      )
                    }
                    disabled={String(lab.draft.subtitle_layout || "horizontal") !== "vertical"}
                  />
                </label>
                <label>
                  对齐
                  <select
                    value={String(lab.draft.subtitle_align || "center")}
                    onChange={(e) => {
                      const align = e.target.value;
                      const x = align === "left" ? 18 : align === "right" ? 82 : 50;
                      lab.setDraft((prev) => ({
                        ...prev,
                        subtitle_align: align,
                        subtitle_x_pct: x,
                      }));
                      lab.setDirty(true);
                    }}
                    disabled={String(lab.draft.subtitle_layout || "horizontal") === "vertical"}
                  >
                    <option value="left">左</option>
                    <option value="center">中</option>
                    <option value="right">右</option>
                  </select>
                </label>
                <label>
                  字幕字体
                  <select
                    value={String(lab.draft.subtitle_font_family || "default")}
                    onChange={(e) => lab.patchDraft("subtitle_font_family", e.target.value)}
                  >
                    {fontOptions.map((f) => (
                      <option key={f.id} value={f.id} disabled={f.available === false}>
                        {f.label}
                        {f.use_hint ? ` · ${f.use_hint}` : ""}
                        {f.available === false ? "（未下载）" : ""}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  竖排侧边
                  <select
                    value={String(lab.draft.subtitle_vertical_side || "left")}
                    onChange={(e) => {
                      const side = e.target.value;
                      lab.setDraft((prev) => ({
                        ...prev,
                        subtitle_vertical_side: side,
                        subtitle_x_pct: side === "right" ? 82 : 18,
                      }));
                      lab.setDirty(true);
                    }}
                    disabled={String(lab.draft.subtitle_layout || "horizontal") !== "vertical"}
                  >
                    <option value="left">左侧</option>
                    <option value="right">右侧</option>
                  </select>
                </label>
                <label>
                  字幕方式
                  <select
                    value={String(lab.draft.subtitle_burn || "burn_mono")}
                    onChange={(e) => lab.patchDraft("subtitle_burn", e.target.value)}
                  >
                    <option value="burn_mono">烧录单语</option>
                    <option value="burn_dual">烧录双语</option>
                    <option value="external">外挂字幕</option>
                  </select>
                </label>
              </div>
              <p className="hint">
                字幕动效 / 竖排开关在右侧「视觉层与字幕动效」；此处调颜色字号与烧录方式。
              </p>
            </div>
          </div>
        </section>

        <section className="rule-card rule-card--span-2">
          <h3>声音与语言</h3>
          <div className="grid3 rule-voice-grid">
            <label>
              旁白语言
              <LangCombobox
                value={String(lab.draft.voice_lang || "zh")}
                onChange={(code) => lab.patchDraft("voice_lang", code)}
                languages={languages}
                includeNone
                noneLabel="无旁白"
              />
            </label>
            <label>
              字幕语言
              <LangCombobox
                value={String(lab.draft.subtitle_lang || "zh")}
                onChange={(code) => lab.patchDraft("subtitle_lang", code)}
                languages={languages}
                includeNone
                noneLabel="无字幕"
              />
            </label>
            <label>
              音色来源
              <select
                value={String(lab.draft.tts_provider || "edge")}
                onChange={(e) => {
                  const next = e.target.value === "clone" ? "clone" : "edge";
                  lab.setDraft((prev) => ({
                    ...prev,
                    tts_provider: next,
                    voice_pack:
                      next === "clone"
                        ? String(prev.voice_pack || "aunt_slow")
                        : prev.voice_pack,
                  }));
                  lab.setDirty(true);
                }}
              >
                <option value="edge">系统自然音色（Edge）</option>
                <option value="clone">本地克隆音色</option>
              </select>
            </label>
            {String(lab.draft.tts_provider || "edge") !== "clone" ? (
              <label className="rule-span-2">
                Edge 音色
                <select
                  value={String(lab.draft.tts_voice || "zh-CN-XiaoxiaoNeural")}
                  onChange={(e) => lab.patchDraft("tts_voice", e.target.value)}
                >
                  {(() => {
                    const cur = String(lab.draft.tts_voice || "zh-CN-XiaoxiaoNeural");
                    const has = lab.edgeVoices.some((v) => v.id === cur);
                    const opts = has
                      ? lab.edgeVoices
                      : [{ id: cur, locale: "", gender: "", label: cur }, ...lab.edgeVoices];
                    return opts.map((v) => {
                      const gender =
                        v.gender === "Female"
                          ? "女"
                          : v.gender === "Male"
                            ? "男"
                            : v.gender || "";
                      const main = [v.label || v.id, gender].filter(Boolean).join(" · ");
                      return (
                        <option key={v.id} value={v.id} title={v.id}>
                          {main}
                          {v.locale ? ` · ${v.locale}` : ""}
                        </option>
                      );
                    });
                  })()}
                </select>
                <span className="hint" style={{ display: "block", marginTop: 4 }}>
                  <label style={{ display: "inline-flex", alignItems: "center", gap: 6, cursor: "pointer" }}>
                    <input
                      type="checkbox"
                      checked={lab.edgeShowAll}
                      onChange={(e) => lab.setEdgeShowAll(e.target.checked)}
                    />
                    显示全部语言音色（{lab.edgeVoicesTotal || "…"} 种）
                  </label>
                  {!lab.edgeShowAll ? (
                    <span> · 当前按旁白语言筛选，共 {lab.edgeVoices.length} 种</span>
                  ) : null}
                </span>
              </label>
            ) : null}
            <label>
              语速
              <input
                value={String(lab.draft.narration_rate || "-8%")}
                onChange={(e) => lab.patchDraft("narration_rate", e.target.value)}
              />
            </label>
            <label>
              音量
              <input
                value={String(lab.draft.narration_volume || "+12%")}
                onChange={(e) => lab.patchDraft("narration_volume", e.target.value)}
              />
            </label>
            <label className="rule-range-field">
              配乐音量
              <input
                type="range"
                min={0}
                max={1}
                step={0.01}
                value={Number(lab.draft.bgm_volume || 0.48)}
                onChange={(e) => lab.patchDraft("bgm_volume", Number(e.target.value))}
              />
            </label>
          </div>
        </section>

        <section className="rule-card rule-card--span-2">
          <h3>音色管理</h3>
          <VoiceTtsPanel
            key={`voice-tts-${activeCustomerId ?? "none"}`}
            notify={notify}
            pickFile={pickFile}
            activeCustomerId={activeCustomerId}
            ruleLink={{
              provider: String(lab.draft.tts_provider || "edge") === "clone" ? "clone" : "edge",
              voicePack: String(lab.draft.voice_pack || "aunt_slow"),
              edgeVoice: String(lab.draft.tts_voice || "zh-CN-XiaoxiaoNeural"),
              onDraftChange: (patch) => {
                if (patch.tts_provider != null) lab.patchDraft("tts_provider", patch.tts_provider);
                if (patch.voice_pack != null) lab.patchDraft("voice_pack", patch.voice_pack);
                if (patch.tts_voice != null) lab.patchDraft("tts_voice", patch.tts_voice);
              },
            }}
          />
        </section>
      </div>

      <details className="rule-lab-packaging">
        <summary>
          安全包装与更多转场（默认关 · 精品可开）
          {packCount === 0 ? " · 全部关" : ` · 已开 ${packCount} 项`}
        </summary>
        <p className="hint">
          日更请保持「无 / 关」。蒙版与字幕动效已移到上方「视觉层」；此处为开场/片尾/LUT 与完整 xfade 列表。
        </p>
        <div className="grid3">
          <label>
            开场安全闪入
            <select
              value={String(lab.draft.intro_punch || "none")}
              onChange={(e) => lab.patchDraft("intro_punch", e.target.value)}
            >
              <option value="none">无（默认）</option>
              <option value="soft">轻闪入 ≤0.4s</option>
            </select>
          </label>
          <label>
            物品名微入场
            <select
              value={String(lab.draft.item_label_motion || "none")}
              onChange={(e) => lab.patchDraft("item_label_motion", e.target.value)}
            >
              <option value="none">无（默认）</option>
              <option value="fade">淡入 ≤0.25s</option>
            </select>
          </label>
          <label>
            片尾名片条
            <select
              value={String(lab.draft.end_card || "none")}
              onChange={(e) => lab.patchDraft("end_card", e.target.value)}
            >
              <option value="none">无（默认）</option>
              <option value="simple">简单条（旁白结束后）</option>
            </select>
          </label>
          <label>
            轻调色 LUT
            <select
              value={String(lab.draft.color_lut || "off")}
              onChange={(e) => lab.patchDraft("color_lut", e.target.value)}
            >
              <option value="off">关（默认）</option>
              <option value="light">轻（不伤虚焦门禁）</option>
            </select>
          </label>
          <label>
            同 Plan 多语言
            <select
              value={String(lab.draft.plan_lang_reuse || "off")}
              onChange={(e) => lab.patchDraft("plan_lang_reuse", e.target.value)}
            >
              <option value="off">关（默认）</option>
              <option value="on">开（允许复用画面轨）</option>
            </select>
          </label>
          <label>
            镜头转场（完整列表）
            <select
              value={String(lab.draft.clip_transition || "none")}
              onChange={(e) => lab.patchDraft("clip_transition", e.target.value)}
            >
              <option value="none">硬切（默认）</option>
              {xfadeList.map((name) => (
                <option key={name} value={name}>
                  {transitionLabel(name)}
                </option>
              ))}
            </select>
          </label>
          <label>
            转场时长秒
            <input
              type="number"
              min={0.05}
              max={0.8}
              step={0.05}
              value={Number(lab.draft.clip_transition_duration_sec ?? 0.4)}
              onChange={(e) =>
                lab.patchDraft("clip_transition_duration_sec", Number(e.target.value))
              }
              disabled={String(lab.draft.clip_transition || "none") === "none"}
            />
          </label>
        </div>
      </details>

      <div className="rule-primary-action">
        <button
          type="button"
          className="primary"
          disabled={Boolean(lab.busy)}
          onClick={() => void lab.confirmAndSave()}
        >
          {lab.busy === "confirm" ? "正在保存…" : "保存并启用"}
        </button>
        <button
          type="button"
          disabled={Boolean(lab.busy) || lab.dirty || !dryRunId}
          title={lab.dirty ? "请先保存并启用" : !dryRunId ? "请先保存并启用一条规则" : undefined}
          onClick={() => {
            if (lab.dirty) {
              notify("请先「保存并启用」再预览选片", "warn");
              return;
            }
            if (!dryRunId) {
              notify("请先在规则实验室保存并启用", "warn");
              return;
            }
            runDryRunWithRule?.(dryRunId);
          }}
        >
          用当前规则预览选片
        </button>
        {onGoTasks ? (
          <button type="button" className="ghost" onClick={onGoTasks}>
            去任务
          </button>
        ) : null}
        <span className="hint">{lab.dirty ? "有尚未保存的修改" : "当前设置已保存"}</span>
      </div>
    </>
  );
}

export function RuleAdvancedPanel({ lab, orientation }: { lab: LabApi; orientation: Orientation }) {
  return (
    <details className="rule-lab-advanced" data-guide="rule-history">
      <summary>更多设置、文字生成与历史版本</summary>
      <div className="rule-lab-grid">
        <aside className="rule-lab-list">
          <div className="rule-lab-list-head">
            <h3>已存版本</h3>
            <button type="button" className="ghost" disabled={!!lab.busy} onClick={() => lab.startNew()}>
              新建
            </button>
          </div>
          <label className="check">
            <input
              type="checkbox"
              checked={lab.rotationPolicy}
              disabled={!!lab.busy}
              onChange={(e) => void lab.setCustomerRotation(e.target.checked)}
            />
            本客户启用轮换
          </label>
          <ul>
            {lab.rules.map((r: RuleRow) => (
              <li key={r.id}>
                <button
                  type="button"
                  className={lab.selected?.id === r.id ? "is-active" : undefined}
                  onClick={() => lab.loadRule(r)}
                >
                  <span>
                    {r.name} · r{r.revision}
                    {r.id === lab.activeId ? " · 启用中" : ""}
                  </span>
                  <small>
                    {r.status === "approved" ? "已保存" : r.status === "draft" ? "草稿" : "已归档"}
                  </small>
                </button>
                {r.status !== "archived" ? (
                  <label className="check rule-rotation-switch">
                    <input
                      type="checkbox"
                      checked={Boolean(r.rotation_enabled) && r.status === "approved"}
                      disabled={!!lab.busy || r.status !== "approved"}
                      onChange={(e) => {
                        e.stopPropagation();
                        void lab.setRuleRotation(r.id, e.target.checked);
                      }}
                    />
                    {r.status === "approved" ? "参加轮换" : "保存后可轮换"}
                  </label>
                ) : null}
              </li>
            ))}
            {!lab.rules.length ? <li className="muted">暂无规则版本</li> : null}
          </ul>
          <p className="hint">
            轮换池 {lab.rotationPoolCount} 条（仅已保存且打开开关；草稿不参与）
          </p>
        </aside>

        <div className="rule-lab-editor">
          <label>
            规则名称
            <input
              value={lab.name}
              onChange={(e) => {
                lab.setName(e.target.value);
                lab.setDirty(true);
              }}
            />
          </label>
          <label>
            用自己的话描述要求
            <textarea
              rows={4}
              value={lab.sourceText}
              placeholder="例如：节奏快一点，多拍产品细节，不要人物正脸，旁白朴实，大概 25 秒"
              onChange={(e) => {
                lab.setSourceText(e.target.value);
                lab.setDirty(true);
              }}
            />
          </label>
          <div className="actions">
            <button type="button" disabled={!!lab.busy} onClick={() => void lab.parseWithAi()}>
              {lab.busy === "parse" ? "理解中…" : "本地 AI 理解"}
            </button>
            <button type="button" disabled={!!lab.busy} onClick={() => void lab.validateLocal()}>
              {lab.busy === "validate" ? "校验中…" : "校验硬锁"}
            </button>
            <button type="button" disabled={!!lab.busy} onClick={() => void lab.saveDraft()}>
              {lab.busy === "save" ? "保存中…" : "保存草稿"}
            </button>
            <button type="button" disabled={!!lab.busy} onClick={() => void lab.draftsFromPack()}>
              {lab.busy === "from-pack" ? "生成中…" : "从词池生成草稿"}
            </button>
          </div>
          <p className="hint">
            内容面只钉死本规则用词池哪一类标题/词条；改文案请改词池对应区块。从词池生成的是草稿，不会自动启用。
          </p>
          {lab.confidence != null ? (
            <p className="muted">AI 置信度：{(lab.confidence * 100).toFixed(0)}%</p>
          ) : null}

          <h3 className="section-title">结构化字段（可改）</h3>
          <p className="hint">节奏 / 旁白语气与上方「成片气质」同步；此处可细调。</p>
          <div className="grid3">
            <label>
              词池内容面
              <select
                value={String(lab.draft.content_facet ?? "")}
                onChange={(e) => lab.patchDraft("content_facet", e.target.value || null)}
              >
                <option value="">默认（不钉死，跟随任务/自动选题）</option>
                {lab.contentFacets
                  .filter((f) => f.name !== "default")
                  .map((f) => (
                    <option key={f.name} value={f.name} disabled={!f.usable}>
                      {f.label}
                      {f.usable ? ` · ${f.title_count}条标题` : " · 无标题不可用"}
                    </option>
                  ))}
              </select>
            </label>
            <label>
              主题
              <input
                value={String(lab.draft.theme ?? "")}
                onChange={(e) => lab.patchDraft("theme", e.target.value || null)}
              />
            </label>
            <label>
              分类
              <input
                value={String(lab.draft.category ?? "")}
                onChange={(e) => lab.patchDraft("category", e.target.value || null)}
              />
            </label>
            <label>
              模板倾向
              <select
                value={String(lab.draft.template_preference ?? "")}
                onChange={(e) => lab.patchDraft("template_preference", e.target.value || null)}
              >
                <option value="">（跟随任务/行业）</option>
                <option value="default-vertical">{templatePreferenceLabel("default-vertical")}</option>
                <option value="fast-ship">{templatePreferenceLabel("fast-ship")}</option>
                <option value="stable-product">{templatePreferenceLabel("stable-product")}</option>
              </select>
            </label>
            <label>
              目标时长（秒）
              <input
                type="number"
                min={8}
                max={90}
                value={lab.draft.target_duration_sec == null ? "" : Number(lab.draft.target_duration_sec)}
                onChange={(e) =>
                  lab.patchDraft(
                    "target_duration_sec",
                    e.target.value === "" ? null : Number(e.target.value),
                  )
                }
              />
            </label>
            <label>
              节奏
              <select
                value={String(lab.draft.pace || "normal")}
                onChange={(e) => lab.patchDraft("pace", e.target.value)}
              >
                <option value="slow">慢</option>
                <option value="normal">正常</option>
                <option value="fast">快</option>
              </select>
            </label>
            <label>
              旁白语气
              <select
                value={String(lab.draft.narration_tone || "plain")}
                onChange={(e) => lab.patchDraft("narration_tone", e.target.value)}
              >
                <option value="plain">朴实</option>
                <option value="warm">温暖</option>
                <option value="energetic">有劲</option>
                <option value="passionate">激情起伏</option>
                <option value="professional">专业</option>
              </select>
            </label>
            <label>
              配乐结尾淡出
              <select
                value={String(lab.draft.bgm_fade_out || "standard")}
                onChange={(e) => lab.patchDraft("bgm_fade_out", e.target.value)}
              >
                <option value="off">关闭</option>
                <option value="short">短（约 1.5 秒）</option>
                <option value="standard">标准（约 3 秒）</option>
                <option value="long">长（约 5 秒）</option>
              </select>
            </label>
            <label>
              偏好场景标签
              <input
                value={asList(lab.draft.prefer_semantic_labels)}
                placeholder="近景, 仓库, 装车"
                onChange={(e) => lab.patchDraft("prefer_semantic_labels", parseList(e.target.value))}
              />
            </label>
            <label>
              排除场景标签
              <input
                value={asList(lab.draft.exclude_semantic_labels)}
                onChange={(e) => lab.patchDraft("exclude_semantic_labels", parseList(e.target.value))}
              />
            </label>
            <label>
              画质加严（≥0.35）
              <input
                type="number"
                min={0.35}
                max={1}
                step={0.01}
                value={lab.draft.min_cliplet_quality == null ? "" : Number(lab.draft.min_cliplet_quality)}
                onChange={(e) =>
                  lab.patchDraft(
                    "min_cliplet_quality",
                    e.target.value === "" ? null : Number(e.target.value),
                  )
                }
              />
            </label>
          </div>
          <div className="grid2">
            <label className="check">
              <input
                type="checkbox"
                checked={Boolean(lab.draft.exclude_people_faces)}
                onChange={(e) => lab.patchDraft("exclude_people_faces", e.target.checked)}
              />
              尽量避开人物正脸/人像类镜头
            </label>
            <label className="check">
              <input
                type="checkbox"
                checked={Boolean(lab.draft.strict_semantic_v1)}
                onChange={(e) => lab.patchDraft("strict_semantic_v1", e.target.checked)}
              />
              启用严格语义选片（可开不可放松已开任务）
            </label>
          </div>
          <label>
            理解备注
            <textarea
              rows={2}
              value={String(lab.draft.notes ?? "")}
              onChange={(e) => lab.patchDraft("notes", e.target.value)}
            />
          </label>
          <h3 className="section-title">物品名称图层</h3>
          <div className="grid3">
            <label className="check">
              <input
                type="checkbox"
                checked={Boolean(lab.draft.item_label_enabled)}
                onChange={(e) => lab.patchDraft("item_label_enabled", e.target.checked)}
              />
              启用（仍须官方证据 + 当前 strict 画面共同通过）
            </label>
            <label>
              侧边
              <select
                value={String(lab.draft.item_label_side || "left")}
                onChange={(e) => lab.patchDraft("item_label_side", e.target.value)}
              >
                <option value="left">左侧</option>
                <option value="right">右侧</option>
              </select>
            </label>
            <label>
              字号
              <input
                type="number"
                min={36}
                max={88}
                value={Number(lab.draft.item_label_font_size || 56)}
                onChange={(e) => lab.patchDraft("item_label_font_size", Number(e.target.value))}
              />
            </label>
            <label>
              安全区顶部
              <input
                type="number"
                min={300}
                max={1100}
                value={Number(lab.draft.item_label_safe_top || 360)}
                onChange={(e) => lab.patchDraft("item_label_safe_top", Number(e.target.value))}
              />
            </label>
            <label>
              安全区底部
              <input
                type="number"
                min={700}
                max={1500}
                value={Number(lab.draft.item_label_safe_bottom || 1320)}
                onChange={(e) => lab.patchDraft("item_label_safe_bottom", Number(e.target.value))}
              />
            </label>
          </div>

          {(lab.rejected.length > 0 || lab.clamped.length > 0 || lab.warnings.length > 0) && (
            <div className="rule-lab-conflicts">
              <h4>冲突与钳制</h4>
              {lab.rejected.map((r: string) => (
                <p key={r} className="err-line">
                  拒绝：{r}
                </p>
              ))}
              {lab.clamped.map((c, i) => (
                <p key={i} className="warn-line">
                  钳制：{typeof c === "string" ? c : `${c.field} — ${c.reason} (${c.action})`}
                </p>
              ))}
              {lab.warnings.map((w: string) => (
                <p key={w} className="muted">
                  提示：{w}
                </p>
              ))}
            </div>
          )}

          <div className="actions">
            <button type="button" className="ghost" disabled={!!lab.busy} onClick={() => void lab.confirmAndSave()}>
              {lab.busy === "confirm" ? "保存中…" : "保存并启用（捷径）"}
            </button>
            <button
              type="button"
              className="ghost"
              disabled={!!lab.busy || !lab.selected}
              onClick={() => void lab.archiveSelected()}
            >
              归档
            </button>
            <button
              type="button"
              className="ghost"
              disabled={!!lab.busy || !lab.selected}
              onClick={() => void lab.copySelected()}
            >
              复制为草稿
            </button>
            <button
              type="button"
              className="ghost"
              disabled={!!lab.busy || lab.selected?.status !== "draft"}
              onClick={() => void lab.deleteSelected()}
            >
              删除草稿
            </button>
          </div>
          <p className="hint muted">主路径底部「保存并启用」为唯一主按钮；此处为高级捷径。</p>
          <p className="hint muted">
            画幅：{orientation === "landscape" ? "横屏" : "竖屏"} · 与启用规则同步写入 Job 冻结
          </p>
        </div>

        <aside className="rule-lab-locks">
          <h3>质量保护</h3>
          <ul>
            {lab.hardLocks.map((h) => (
              <li key={h.id}>
                <strong>{h.label}</strong>
                <span>{h.detail}</span>
              </li>
            ))}
          </ul>
          <p className="muted">这些保护用于避免模糊、字幕错位或成片不完整。</p>
        </aside>
      </div>
    </details>
  );
}
