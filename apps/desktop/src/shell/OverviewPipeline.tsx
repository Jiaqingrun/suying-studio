import type { Tab } from "../types";

type Node = {
  id: Tab;
  label: string;
  count: number;
  blocked?: boolean;
  warn?: boolean;
};

type Props = {
  nodes: Node[];
  onJump: (t: Tab) => void;
  pathBlocked?: boolean;
};

export function OverviewPipeline({ nodes, onJump, pathBlocked }: Props) {
  return (
    <div className="pipeline" aria-label="产线作战泳道">
      {pathBlocked ? (
        <div className="pipeline-banner warn">路径异常：生产已闸停，请先修复片库/成片路径</div>
      ) : null}
      <div className="pipeline-lane">
        {nodes.map((n, i) => (
          <div key={n.id} className="pipeline-step">
            {i > 0 ? <span className="pipeline-arrow" aria-hidden /> : null}
            <button
              type="button"
              className={`pipeline-node${n.blocked ? " is-blocked" : ""}${n.warn ? " is-warn" : ""}`}
              onClick={() => onJump(n.id)}
            >
              <span className="pipeline-label">{n.label}</span>
              <span className="pipeline-count">{n.count}</span>
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
