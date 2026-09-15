import type { ReactNode } from "react";

/** AV 概念图底栏四格语汇（装饰性信息架构，不改变九 Tab 合同） */
const FEATURES: Array<{ title: string; blurb: string; icon: ReactNode }> = [
  {
    title: "专业调色",
    blurb: "与达芬奇工作流对齐的校色语汇",
    icon: (
      <svg className="av-feature-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.4" aria-hidden>
        <circle cx="12" cy="12" r="8" />
        <path d="M12 4v16M4 12h16" />
      </svg>
    ),
  },
  {
    title: "镜头审校",
    blurb: "逐镜批注与证据链抽检",
    icon: (
      <svg className="av-feature-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.4" aria-hidden>
        <rect x="3" y="6" width="18" height="12" rx="2" />
        <path d="M8 12h8" />
      </svg>
    ),
  },
  {
    title: "安全可控",
    blurb: "权限、水印与发布门禁",
    icon: (
      <svg className="av-feature-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.4" aria-hidden>
        <path d="M12 3l8 4v5c0 5-3.5 8.5-8 9-4.5-.5-8-4-8-9V7l8-4z" />
      </svg>
    ),
  },
  {
    title: "优雅发布",
    blurb: "画廊归档与一键触达",
    icon: (
      <svg className="av-feature-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.4" aria-hidden>
        <path d="M4 12l16-7-7 16-2-6-7-3z" />
      </svg>
    ),
  },
];

export function AvFeatureBar() {
  return (
    <footer className="av-feature-bar" aria-label="工作室能力">
      {FEATURES.map((f) => (
        <div key={f.title}>
          {f.icon}
          <strong>{f.title}</strong>
          <span>{f.blurb}</span>
        </div>
      ))}
    </footer>
  );
}
