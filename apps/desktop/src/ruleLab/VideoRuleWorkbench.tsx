import type { LangCatalogItem, NotifyFn } from "../pages/pageTypes";
import { RuleAdvancedPanel, RuleMainEditor, RuleStatusHeader } from "./RulePanels";
import type { Orientation } from "./types";
import { useRuleLabState } from "./useRuleLabState";

export type { Orientation } from "./types";

type Props = {
  notify: NotifyFn;
  activeCustomerId?: number | null;
  customerName?: string;
  selectedRuleId: number | null;
  onSelectedRuleIdChange: (id: number | null) => void;
  onGoTasks?: () => void;
  runDryRunWithRule?: (ruleId: number | null) => void;
  orientation: Orientation;
  onOrientationChange: (value: Orientation) => void;
  pickFile: () => Promise<string | null>;
  languages: LangCatalogItem[];
};

export function VideoRuleWorkbench({
  notify,
  activeCustomerId = null,
  customerName = "",
  selectedRuleId,
  onSelectedRuleIdChange,
  onGoTasks,
  runDryRunWithRule,
  orientation,
  onOrientationChange,
  pickFile,
  languages,
}: Props) {
  const lab = useRuleLabState({
    notify,
    activeCustomerId,
    selectedRuleId,
    onSelectedRuleIdChange,
    orientation,
    onOrientationChange,
  });

  return (
    <div className="rule-lab">
      <div className="rule-orientation-switch" role="group" aria-label="成片画幅">
        {(["portrait", "landscape"] as Orientation[]).map((value) => (
          <button
            key={value}
            type="button"
            className={orientation === value ? "primary" : undefined}
            onClick={() => lab.changeOrientation(value)}
          >
            {value === "portrait" ? "竖屏 1080×1920" : "横屏 1920×1080"}
          </button>
        ))}
      </div>
      <RuleStatusHeader lab={lab} customerName={customerName} orientation={orientation} />
      <RuleMainEditor
        lab={lab}
        customerName={customerName}
        orientation={orientation}
        languages={languages}
        notify={notify}
        activeCustomerId={activeCustomerId}
        pickFile={pickFile}
        onGoTasks={onGoTasks}
        runDryRunWithRule={runDryRunWithRule}
      />
      <RuleAdvancedPanel lab={lab} orientation={orientation} />
    </div>
  );
}
