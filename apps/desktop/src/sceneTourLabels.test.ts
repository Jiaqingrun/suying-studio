import { describe, expect, it } from "vitest";

import {
  assetCategoryLabel,
  contentCategoryLabel,
  pipelinePhaseLabel,
  productionCategoryLabel,
  productionThemeLabel,
  templatePreferenceLabel,
} from "./sceneTourLabels";

describe("productionThemeLabel", () => {
  it("maps internal content keys to Chinese", () => {
    expect(productionThemeLabel("default")).toBe("日常日更");
    expect(productionThemeLabel("scene_tour")).toBe("跟镜精品");
    expect(productionThemeLabel("premium")).toBe("精品样式");
  });

  it("passes through Chinese industry themes", () => {
    expect(productionThemeLabel("电动工具与五金工具")).toBe("电动工具与五金工具");
    expect(productionThemeLabel("配送")).toBe("配送");
  });

  it("hides unknown English slugs", () => {
    expect(productionThemeLabel("brands")).toBe("品牌专题");
    expect(productionThemeLabel("mystery_slug")).toBe("未命名主题");
  });
});

describe("contentCategoryLabel", () => {
  it("defaults empty to 日常日更", () => {
    expect(contentCategoryLabel(null)).toBe("日常日更");
    expect(contentCategoryLabel("")).toBe("日常日更");
  });
});

describe("productionCategoryLabel", () => {
  it("aliases theme labeling", () => {
    expect(productionCategoryLabel("scene_tour")).toBe("跟镜精品");
  });
});

describe("pipelinePhaseLabel", () => {
  it("maps worker phases", () => {
    expect(pipelinePhaseLabel("rendering")).toBe("渲染中");
    expect(pipelinePhaseLabel("blocked_clone_runtime")).toBe("音色未就绪");
  });
});

describe("assetCategoryLabel", () => {
  it("maps uncategorized folders", () => {
    expect(assetCategoryLabel("uncategorized")).toBe("未分类");
    expect(assetCategoryLabel("")).toBe("未分类");
  });
});

describe("templatePreferenceLabel", () => {
  it("maps template slugs", () => {
    expect(templatePreferenceLabel("default-vertical")).toBe("竖屏默认");
    expect(templatePreferenceLabel(null)).toBe("（跟随任务/行业）");
  });
});
