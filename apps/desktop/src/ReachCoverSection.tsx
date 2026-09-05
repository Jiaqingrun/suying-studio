import type { CoverSlotSpec, ReachPlatform } from "./reachCatalog";

type SlotRow = Record<string, unknown> & {
  index?: number;
  filled?: boolean;
  path?: string | null;
  label?: string;
  aspect?: string;
  width?: number;
  height?: number;
  role?: string;
};

type Props = {
  platforms: ReachPlatform[];
  templates: Array<Record<string, unknown>>;
  editId: string;
  selectedId: string | null;
  newName: string;
  slotCounts: Record<string, number>;
  slotSpecs: Record<string, CoverSlotSpec[]>;
  previewPlat: string;
  previewMsg: string;
  busy: boolean;
  onNewName: (v: string) => void;
  onEditId: (v: string) => void;
  onPreviewPlat: (v: string) => void;
  onCreate: () => void;
  onSeed: () => void;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
  onSetSlot: (platform: string, slotIndex: number) => void;
  onPreview: () => void;
};

function expectedSlotCount(
  plat: string,
  platInfo: ReachPlatform,
  slotCounts: Record<string, number>,
  specs: CoverSlotSpec[],
  detailLen: number,
): number {
  return Math.max(
    slotCounts[plat] ?? 0,
    specs.length,
    platInfo.cover_slots ?? 0,
    detailLen,
    1,
  );
}

function buildSlots(
  detail: SlotRow[],
  specs: CoverSlotSpec[],
  n: number,
): SlotRow[] {
  const byIndex = new Map<number, SlotRow>();
  for (const row of detail) {
    const idx = Number(row.index ?? 0);
    byIndex.set(idx, row);
  }
  return Array.from({ length: n }, (_, i) => {
    const existing = byIndex.get(i);
    if (existing) return existing;
    const sp = specs[i];
    return {
      index: i,
      filled: false,
      path: null,
      label: sp?.label || `槽${i + 1}`,
      aspect: sp?.aspect || "",
      width: sp?.width || 0,
      height: sp?.height || 0,
      role: sp?.role || "",
    };
  });
}

export function ReachCoverSection({
  platforms,
  templates,
  editId,
  selectedId,
  newName,
  slotCounts,
  slotSpecs,
  previewPlat,
  previewMsg,
  busy,
  onNewName,
  onEditId,
  onPreviewPlat,
  onCreate,
  onSeed,
  onSelect,
  onDelete,
  onSetSlot,
  onPreview,
}: Props) {
  const summary = platforms
    .map((p) => {
      const n = Math.max(slotCounts[p.id] ?? 0, slotSpecs[p.id]?.length ?? 0, p.cover_slots ?? 1);
      return `${p.short || p.label}×${n}`;
    })
    .join(" · ");

  const tpl = templates.find((t) => String(t.id) === editId);
  const slotsDetail =
    (tpl?.slots_detail as Record<string, SlotRow[]> | undefined) || {};

  let emptySlots = 0;
  let totalSlots = 0;
  if (editId) {
    for (const platInfo of platforms) {
      const plat = platInfo.id;
      const specs = slotSpecs[plat] || [];
      const detail = slotsDetail[plat] || [];
      const n = expectedSlotCount(plat, platInfo, slotCounts, specs, detail.length);
      const slots = buildSlots(detail, specs, n);
      totalSlots += n;
      emptySlots += slots.filter((s) => !s.filled).length;
    }
  }
  const coverPhase = !templates.length
    ? "empty"
    : !editId
      ? "pick"
      : emptySlots > 0
        ? "filling"
        : !selectedId
          ? "ready_unset"
          : selectedId !== editId
            ? "edit_other"
            : "publish_ready";
  const coverPhaseHint: Record<string, string> = {
    empty: "状态：尚无封面套 — 先新建",
    pick: "状态：请选择要编辑的封面套",
    filling: `状态：补槽中（还差 ${emptySlots}/${totalSlots}）`,
    ready_unset: "状态：槽位已齐 — 点「设为当前发布封面」",
    edit_other: "状态：正在编辑另一套；当前发布仍用已选用模板",
    publish_ready: "状态：当前编辑套 = 发布用 · 槽位已齐",
  };

  return (
    <div className="reach-cover">
      <div className="reach-cover-head">
        <h3>封面设置</h3>
        <p className="hint">所有视频平台只使用 1 张竖版封面；发布严格取「当前选用」模板，不回退物料包封面。</p>
        <p className="reach-cover-summary path">{summary}</p>
        <p className={`cover-phase cover-phase--${coverPhase}`}>{coverPhaseHint[coverPhase]}</p>
      </div>

      <div className="reach-cover-toolbar">
        <input
          value={newName}
          onChange={(e) => onNewName(e.target.value)}
          placeholder="新模板名称，如：工业风-A"
          aria-label="新模板名称"
        />
        <button type="button" className="primary" disabled={busy} onClick={onCreate}>
          新建封面套
        </button>
        <button type="button" disabled={busy || !editId} onClick={onSeed}>
          从物料包灌入空槽
        </button>
        <label className="reach-inline-label">
          <span>编辑套</span>
          <select value={editId} onChange={(e) => onEditId(e.target.value)}>
            {templates.length === 0 && <option value="">暂无模板 — 请先新建</option>}
            {templates.map((t) => (
              <option key={String(t.id)} value={String(t.id)}>
                {String(t.name || t.id)}
                {t.selected ? " · 当前发布用" : ""}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          className="primary"
          disabled={busy || !editId}
          onClick={() => onSelect(editId)}
        >
          设为当前发布封面
        </button>
        <button type="button" disabled={busy || !editId} onClick={() => onDelete(editId)}>
          删除此套
        </button>
      </div>

      {!editId ? (
        <p className="path">新建一套后，按平台把空槽补齐，再点「设为当前发布封面」。</p>
      ) : (
        <div className="reach-cover-grid">
          {platforms.map((platInfo) => {
            const plat = platInfo.id;
            const label = platInfo.short || platInfo.label || plat;
            const specs = slotSpecs[plat] || [];
            const detail = slotsDetail[plat] || [];
            const n = expectedSlotCount(plat, platInfo, slotCounts, specs, detail.length);
            const slots = buildSlots(detail, specs, n);
            const filledN = slots.filter((s) => s.filled).length;
            return (
              <div key={plat} className="reach-cover-plat">
                <div className="reach-cover-plat-head">
                  <strong>{label}</strong>
                  <span className="hint">
                    {filledN}/{n} 已填
                  </span>
                </div>
                <div className="reach-cover-slots">
                  {slots.map((s) => {
                    const idx = Number(s.index ?? 0);
                    const sp = specs[idx] || s;
                    const slotLabel = String(sp.label || `槽${idx + 1}`);
                    const aspect = String(sp.aspect || "");
                    const w = Number(sp.width || 0);
                    const h = Number(sp.height || 0);
                    const role = String(sp.role || "");
                    const filled = Boolean(s.filled);
                    const path = String(s.path || "空槽 — 点击选图");
                    return (
                      <button
                        key={`${plat}-${idx}`}
                        type="button"
                        className={`cover-slot${filled ? " cover-slot--filled" : ""}`}
                        disabled={busy}
                        onClick={() => onSetSlot(plat, idx)}
                        title={[role, path].filter(Boolean).join("\n")}
                      >
                        <span className="cover-slot-name">{slotLabel}</span>
                        <span className="cover-slot-meta">
                          {aspect}
                          {w && h ? ` · ${w}×${h}` : ""}
                        </span>
                        <span className={`cover-slot-state${filled ? " is-ok" : ""}`}>
                          {filled ? "已填" : "空"}
                        </span>
                      </button>
                    );
                  })}
                </div>
              </div>
            );
          })}
        </div>
      )}

      <div className="reach-cover-footer">
        <label className="reach-inline-label">
          <span>预览将用</span>
          <select value={previewPlat} onChange={(e) => onPreviewPlat(e.target.value)}>
            {platforms.map((p) => (
              <option key={p.id} value={p.id}>
                {p.short || p.label || p.id}
              </option>
            ))}
          </select>
        </label>
        <button type="button" disabled={busy} onClick={onPreview}>
          解析封面路径
        </button>
        {selectedId ? (
          <span className="path">当前发布封面套：{selectedId}</span>
        ) : (
          <span className="path">尚未选用模板：发布将被门禁阻止</span>
        )}
      </div>
      {previewMsg ? <p className="path">{previewMsg}</p> : null}
    </div>
  );
}
