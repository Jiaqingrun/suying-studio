import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import type { NotifyFn } from "../pages/pageTypes";
import {
  buildEmptyRules,
  mergeRecommendedCategories,
} from "./defaults";
import type { Orientation, RuleRow, RuleSchemaBundle } from "./types";

type UseRuleLabArgs = {
  notify: NotifyFn;
  activeCustomerId?: number | null;
  selectedRuleId: number | null;
  onSelectedRuleIdChange: (id: number | null) => void;
  orientation: Orientation;
  onOrientationChange: (value: Orientation) => void;
};

export function useRuleLabState({
  notify,
  activeCustomerId = null,
  selectedRuleId,
  onSelectedRuleIdChange,
  orientation,
  onOrientationChange,
}: UseRuleLabArgs) {
  const [busy, setBusy] = useState<string | null>(null);
  const [rules, setRules] = useState<RuleRow[]>([]);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [contentCategory, setContentCategory] = useState("default");
  const [categories, setCategories] = useState<string[]>(["default", "premium"]);
  const [hardLocks, setHardLocks] = useState<Array<{ id: string; label: string; detail: string }>>([]);
  const [schema, setSchema] = useState<RuleSchemaBundle | null>(null);
  const [name, setName] = useState("日常日更");
  const [sourceText, setSourceText] = useState("");
  const [draft, setDraft] = useState<Record<string, unknown>>(() => buildEmptyRules(null, "portrait"));
  const [rejected, setRejected] = useState<string[]>([]);
  const [clamped, setClamped] = useState<Array<Record<string, string> | string>>([]);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [dirty, setDirty] = useState(false);
  const [parseModel, setParseModel] = useState("");
  const [confidence, setConfidence] = useState<number | null>(null);
  const [edgeVoices, setEdgeVoices] = useState<
    Array<{ id: string; locale: string; gender: string; label: string }>
  >([]);
  const [edgeVoicesTotal, setEdgeVoicesTotal] = useState(0);
  const [edgeShowAll, setEdgeShowAll] = useState(false);
  const [rotationPolicy, setRotationPolicy] = useState(true);
  const [rotationPoolCount, setRotationPoolCount] = useState(0);
  const [contentFacets, setContentFacets] = useState<
    Array<{
      name: string;
      label: string;
      keyword_count: number;
      title_count: number;
      hook_count: number;
      titles_sample: string[];
      usable: boolean;
    }>
  >([]);
  const refreshGeneration = useRef(0);
  const dirtyRef = useRef(false);
  dirtyRef.current = dirty;

  const recommendedCategories = schema?.recommended_content_categories?.length
    ? schema.recommended_content_categories
    : ["default", "premium", "scene_tour"];

  const refresh = useCallback(async () => {
    const generation = ++refreshGeneration.current;
    const expectedCustomerId = activeCustomerId;
    const [list, schemaRes, facetsRes] = await Promise.all([
      api.productionRulesList(false, contentCategory, orientation),
      api.productionRulesSchema(),
      api.productionRulesContentFacets().catch(() => ({ facets: [] })),
    ]);
    if (generation !== refreshGeneration.current) return;
    if (
      expectedCustomerId != null &&
      list.customer_id != null &&
      Number(list.customer_id) !== Number(expectedCustomerId)
    ) {
      return;
    }
    const bundle: RuleSchemaBundle = {
      empty_rules: schemaRes.empty_rules || {},
      orientation_defaults: schemaRes.orientation_defaults || {},
      hard_locks: schemaRes.hard_locks || [],
      recommended_content_categories:
        schemaRes.recommended_content_categories ||
        (Array.isArray(schemaRes.metadata?.recommended_content_categories)
          ? (schemaRes.metadata.recommended_content_categories as string[])
          : ["default", "premium"]),
      fx_assets: (schemaRes as { fx_assets?: RuleSchemaBundle["fx_assets"] }).fx_assets,
    };
    setSchema(bundle);
    setHardLocks(bundle.hard_locks);
    const rows = (list.rules || []) as RuleRow[];
    setRules(rows);
    setActiveId(list.active_id ?? null);
    setCategories(mergeRecommendedCategories(list.categories || [], bundle.recommended_content_categories));
    setRotationPolicy(list.rotation_policy !== false);
    setRotationPoolCount((list.rotation_pool || []).length);
    setContentFacets(Array.isArray(facetsRes.facets) ? facetsRes.facets : []);
    if (!dirtyRef.current) {
      const preferId = selectedRuleId;
      const preferred =
        (preferId != null ? rows.find((r) => r.id === preferId) : null) ||
        rows.find((r) => r.id === list.active_id) ||
        null;
      if (preferred) {
        onSelectedRuleIdChange(preferred.id);
        setName(preferred.name || "未命名规则");
        setSourceText(preferred.source_text || "");
        setDraft({
          ...buildEmptyRules(bundle, orientation),
          ...(preferred.effective_rules || {}),
          orientation,
        });
        setRejected((preferred.rejected as string[]) || []);
        setClamped(preferred.clamped || []);
        setWarnings((preferred.parse_warnings as string[]) || []);
        setConfidence(null);
      }
    }
  }, [contentCategory, orientation, activeCustomerId, onSelectedRuleIdChange, selectedRuleId]);

  useEffect(() => {
    refreshGeneration.current += 1;
    setRules([]);
    setActiveId(null);
    setSourceText("");
    setDraft(buildEmptyRules(schema, orientation));
    setRejected([]);
    setClamped([]);
    setWarnings([]);
    setDirty(false);
    setConfidence(null);
    setName("日常日更");
    onSelectedRuleIdChange(null);
    void refresh().catch((e) => notify(String(e), "err"));
    // Intentionally not depending on contentCategory — category switches call refresh via switchContentCategory / clone.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeCustomerId, orientation]);

  useEffect(() => {
    void refresh().catch((e) => notify(String(e), "err"));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [contentCategory]);

  useEffect(() => {
    let cancelled = false;
    const lang = String(draft.voice_lang || "zh");
    void api
      .getEdgeVoices({
        lang: edgeShowAll ? undefined : lang === "none" ? "zh" : lang,
        all_locales: edgeShowAll,
      })
      .then((res) => {
        if (cancelled) return;
        setEdgeVoices(res.voices || []);
        setEdgeVoicesTotal(Number(res.total) || (res.voices || []).length);
      })
      .catch((e) => notify(String(e), "err"));
    return () => {
      cancelled = true;
    };
  }, [draft.voice_lang, edgeShowAll, notify]);

  const selected = useMemo(
    () => rules.find((r) => r.id === selectedRuleId) || null,
    [rules, selectedRuleId],
  );

  const activeRow = useMemo(
    () => rules.find((r) => r.id === activeId) || null,
    [rules, activeId],
  );

  function loadRule(row: RuleRow) {
    if (dirty) {
      const ok = window.confirm("当前草稿未保存，切换将丢失编辑，继续？");
      if (!ok) return;
    }
    onSelectedRuleIdChange(row.id);
    setName(row.name || "未命名规则");
    setSourceText(row.source_text || "");
    setDraft({
      ...buildEmptyRules(schema, orientation),
      ...(row.effective_rules || {}),
      orientation,
    });
    setRejected((row.rejected as string[]) || []);
    setClamped(row.clamped || []);
    setWarnings((row.parse_warnings as string[]) || []);
    setDirty(false);
    setConfidence(null);
  }

  function patchDraft(key: string, value: unknown): void;
  function patchDraft( partial: Record<string, unknown>): void;
  function patchDraft(keyOrPartial: string | Record<string, unknown>, value?: unknown) {
    if (typeof keyOrPartial === "string") {
      setDraft((prev) => ({ ...prev, [keyOrPartial]: value }));
    } else {
      setDraft((prev) => ({ ...prev, ...keyOrPartial }));
    }
    setDirty(true);
  }

  function switchContentCategory(nextValue: string) {
    const next = nextValue.trim() || "default";
    if (next === contentCategory) return;
    if (dirty && !window.confirm("当前类别的草稿未保存，切换类别将丢失编辑，继续？")) return;
    setContentCategory(next);
    onSelectedRuleIdChange(null);
    setName(
      next === "premium" ? "精品样式" : next === "scene_tour" ? "跟镜精品" : "日常日更",
    );
    setSourceText("");
    setDraft(buildEmptyRules(schema, orientation));
    setRejected([]);
    setClamped([]);
    setWarnings([]);
    setDirty(false);
    setConfidence(null);
  }

  function changeOrientation(value: Orientation) {
    if (value === orientation) return;
    if (dirty && !window.confirm("切换画幅会放弃当前未保存修改，继续？")) return;
    onOrientationChange(value);
    onSelectedRuleIdChange(null);
    setDraft(buildEmptyRules(schema, value));
    setDirty(false);
  }

  async function parseWithAi() {
    if (sourceText.trim().length < 4) {
      notify("请先用自己的话写清要求", "warn");
      return;
    }
    setBusy("parse");
    try {
      const res = await api.productionRulesParse(sourceText, parseModel || undefined);
      setDraft({
        ...buildEmptyRules(schema, orientation),
        ...(res.effective_rules || {}),
        orientation,
      });
      setRejected(res.rejected || []);
      setClamped(res.clamped || []);
      setWarnings(res.warnings || []);
      setConfidence(typeof res.confidence === "number" ? res.confidence : null);
      setParseModel(String(res.model || parseModel));
      setDirty(true);
      notify("本地 AI 已理解并补齐全部字段，请检查后人工确认保存", "ok");
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setBusy(null);
    }
  }

  async function validateLocal() {
    setBusy("validate");
    try {
      const res = await api.productionRulesValidate(draft);
      setDraft({
        ...buildEmptyRules(schema, orientation),
        ...(res.effective_rules || {}),
        orientation,
      });
      setRejected(res.rejected || []);
      setClamped(res.clamped || []);
      setWarnings(res.warnings || []);
      notify("已校验并钳制硬锁冲突", "ok");
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setBusy(null);
    }
  }

  async function persistCurrentDraft(): Promise<number> {
    if (selected?.status === "draft") {
      const res = await api.productionRulesPatch(selected.id, {
        name,
        source_text: sourceText,
        rules: { ...draft, orientation },
      });
      const id = Number((res.rule as RuleRow).id);
      onSelectedRuleIdChange(id);
      return id;
    }
    const res = await api.productionRulesCreate({
      name,
      content_category: contentCategory,
      orientation,
      source_text: sourceText,
      rules: { ...draft, orientation },
      model: parseModel,
      parse_warnings: warnings,
    });
    const id = Number((res.rule as RuleRow).id);
    onSelectedRuleIdChange(id);
    if (selected?.status === "approved") {
      notify(`已从已批准 r${selected.revision} 新建不可变后续版本`, "info");
    }
    return id;
  }

  async function saveDraft() {
    setBusy("save");
    try {
      await persistCurrentDraft();
      setDirty(false);
      notify("规则草稿已保存", "ok");
      await refresh();
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setBusy(null);
    }
  }

  async function confirmAndSave() {
    setBusy("confirm");
    try {
      const tp = String(draft.tts_provider || "edge").toLowerCase() === "clone" ? "clone" : "edge";
      const pack = String(draft.voice_pack || "aunt_slow").trim() || "aunt_slow";
      const edgeVoice =
        String(draft.tts_voice || "zh-CN-XiaoxiaoNeural").trim() || "zh-CN-XiaoxiaoNeural";
      await api.updateTtsVoice({
        provider: tp,
        voice_pack: pack,
        voice: tp === "edge" ? edgeVoice : undefined,
        update_video_lock: true,
      });
      const id = await persistCurrentDraft();
      await api.productionRulesApprove(id);
      await api.productionRulesActivate(id);
      const voiceLabel = edgeVoices.find((v) => v.id === edgeVoice)?.label || edgeVoice;
      notify(
        `${orientation === "landscape" ? "横屏" : "竖屏"}规则已保存并启用（旁白：${
          tp === "clone" ? `克隆 · ${pack}` : `Edge · ${voiceLabel}`
        }）`,
        "ok",
      );
      setDirty(false);
      await refresh();
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setBusy(null);
    }
  }

  async function archiveSelected() {
    if (!selectedRuleId) return;
    if (!window.confirm("确认归档该规则版本？")) return;
    setBusy("archive");
    try {
      await api.productionRulesArchive(selectedRuleId);
      notify("已归档", "ok");
      onSelectedRuleIdChange(null);
      await refresh();
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setBusy(null);
    }
  }

  async function copySelected() {
    if (!selectedRuleId) return;
    setBusy("copy");
    try {
      const res = await api.productionRulesCopy(selectedRuleId);
      onSelectedRuleIdChange(Number((res.rule as RuleRow).id));
      notify("已复制为新草稿", "ok");
      await refresh();
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setBusy(null);
    }
  }

  async function cloneToPremium() {
    const sourceId = selectedRuleId || activeId;
    if (!sourceId) {
      notify("请先选择或启用一条日更规则再克隆为精品", "warn");
      return;
    }
    if (dirty && !window.confirm("当前有未保存修改，克隆将基于已存版本继续？")) return;
    setBusy("premium");
    try {
      let sourceName = rules.find((r) => r.id === sourceId)?.name;
      if (!sourceName) {
        const got = await api.productionRulesGet(sourceId);
        sourceName = String((got.rule as RuleRow)?.name || "规则");
      }
      const res = await api.productionRulesCopy(sourceId, {
        content_category: "premium",
        name: `${sourceName}·精品`,
      });
      const created = res.rule as RuleRow;
      const newId = Number(created.id);
      setContentCategory("premium");
      onSelectedRuleIdChange(newId);
      setName(created.name || "精品样式");
      setSourceText(created.source_text || "");
      setDraft({
        ...buildEmptyRules(schema, orientation),
        ...(created.effective_rules || {}),
        orientation,
      });
      setRejected((created.rejected as string[]) || []);
      setClamped(created.clamped || []);
      setWarnings((created.parse_warnings as string[]) || []);
      setDirty(false);
      notify("已克隆到精品类别（草稿，尚未启用）", "ok");
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setBusy(null);
    }
  }

  async function deleteSelected() {
    if (!selectedRuleId || selected?.status !== "draft") return;
    if (!window.confirm("物理删除这个未批准草稿？此操作不可恢复。")) return;
    setBusy("delete");
    try {
      await api.productionRulesDelete(selectedRuleId);
      onSelectedRuleIdChange(null);
      notify("未批准草稿已删除", "ok");
      await refresh();
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setBusy(null);
    }
  }

  async function setRuleRotation(ruleId: number, enabled: boolean) {
    setBusy("rotation");
    try {
      await api.productionRulesSetRotation(ruleId, enabled);
      notify(enabled ? "已加入轮换池" : "已移出轮换池", "ok");
      await refresh();
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setBusy(null);
    }
  }

  async function setCustomerRotation(enabled: boolean) {
    setBusy("rotation-policy");
    try {
      await api.productionRulesSetRotationPolicy(enabled);
      setRotationPolicy(enabled);
      notify(enabled ? "已开启规则轮换" : "已关闭规则轮换", "ok");
      await refresh();
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setBusy(null);
    }
  }

  async function draftsFromPack(facets?: string[]) {
    setBusy("from-pack");
    try {
      const res = await api.productionRulesDraftsFromPack({
        content_category: contentCategory,
        orientation,
        facets: facets && facets.length ? facets : undefined,
      });
      const n = Number(res.created_count || 0);
      notify(n > 0 ? `已从词池生成 ${n} 条草稿（未启用）` : "没有可生成的新内容面（已有绑定或词池为空）", n > 0 ? "ok" : "info");
      await refresh();
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setBusy(null);
    }
  }

  function startNew() {
    if (dirty && !window.confirm("放弃当前编辑？")) return;
    onSelectedRuleIdChange(null);
    setName(
      contentCategory === "premium"
        ? "精品样式"
        : contentCategory === "scene_tour"
          ? "跟镜精品"
          : "日常日更",
    );
    setSourceText("");
    setDraft(buildEmptyRules(schema, orientation));
    setRejected([]);
    setClamped([]);
    setWarnings([]);
    setDirty(false);
  }

  return {
    busy,
    rules,
    activeId,
    activeRow,
    contentCategory,
    categories,
    recommendedCategories,
    hardLocks,
    schema,
    name,
    setName,
    sourceText,
    setSourceText,
    draft,
    setDraft,
    rejected,
    clamped,
    warnings,
    dirty,
    setDirty,
    confidence,
    edgeVoices,
    edgeVoicesTotal,
    edgeShowAll,
    setEdgeShowAll,
    selected,
    loadRule,
    patchDraft,
    switchContentCategory,
    changeOrientation,
    parseWithAi,
    validateLocal,
    saveDraft,
    confirmAndSave,
    archiveSelected,
    copySelected,
    cloneToPremium,
    deleteSelected,
    startNew,
    refresh,
    rotationPolicy,
    rotationPoolCount,
    contentFacets,
    setRuleRotation,
    setCustomerRotation,
    draftsFromPack,
  };
}
