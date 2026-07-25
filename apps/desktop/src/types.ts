export type Tab =
  | "overview"
  | "produce"
  | "assets"
  | "review"
  | "pack"
  | "publish"
  | "reach"
  | "ops"
  | "assistant";

export type FlashKind = "ok" | "err" | "info" | "warn";

export const TABS: Array<[Tab, string, string]> = [
  ["overview", "总览", "01"],
  ["produce", "生产", "02"],
  ["assets", "素材", "03"],
  ["review", "审片", "04"],
  ["pack", "物料", "05"],
  ["publish", "发布", "06"],
  ["reach", "触达", "07"],
  ["ops", "运维", "08"],
  ["assistant", "助手", "09"],
];

export const TAB_BLURB: Record<Tab, string> = {
  overview: "一眼看清今日产线与阻塞",
  produce: "发射任务与内容日历",
  assets: "片库路径、扫描与品牌",
  review: "抽检成片、通过或重渲",
  pack: "表达设置与物料包导出",
  publish: "复制文案、访达拖传",
  reach: "配额、Chrome 与自动上传",
  ops: "向量化、密钥与同步",
  assistant: "本地 AI 助手",
};
