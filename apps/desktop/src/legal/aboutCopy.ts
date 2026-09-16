/** Wired from docs/legal/APP_ABOUT_COPY_DRAFT.zh-CN.md — keep in sync when copy changes. */

export const ABOUT_PRODUCT_NAME = "速影 Studio";

export const ABOUT_COPYRIGHT_HOLDER = "清闰 / QR（合同乙方以销售合同为准）";

export const ABOUT_BLURB =
  "本地智能混剪工作台：素材入库、成片生产、审片、发布物料与人在回路触达。";

export const ABOUT_COPYRIGHT_NOTE =
  "版权主体待销售合同终裁；本页为工程占位。";

export const ABOUT_SECTIONS: Array<{ title: string; paragraphs: string[] }> = [
  {
    title: "开源与第三方致谢",
    paragraphs: [
      "本产品使用 FFmpeg（GPL）、Ollama（MIT）、本地大模型（Apache-2.0）等组件。完整清单见离线 legal 组件中的 THIRD_PARTY_MANIFEST.json / THIRD_PARTY_NOTICES.zh-CN.md；GPL/LGPL 对应源码要约随 legal 组件提供。",
      "图形表情：Twemoji（CC-BY-4.0）。字体：以 SIL OFL 等许可随包或系统字体为准。",
    ],
  },
  {
    title: "语音合成提示",
    paragraphs: [
      "默认旁白（Edge）：口播文本经微软 Edge 在线语音服务合成；第三方云语音，受微软服务条款约束；非全离线能力。",
      "本地克隆（可选）：依赖本机 F5 Runtime Kit；模型权重多为 CC-BY-NC（非商用）；未清权前不得冒充可任意商用再分发。",
    ],
  },
  {
    title: "隐私与数据（摘要）",
    paragraphs: [
      "片库、成片、Chrome 发布配置与消息摘要默认保存在本机；不设默认公有云上传。可选 ntfy 通知仅发送脱敏摘要。详见仓内 docs/legal/PRIVACY_NOTICE.zh-CN.md 与销售合同。",
    ],
  },
  {
    title: "AI 标识",
    paragraphs: [
      "成片描述默认不附加 AI 生成披露行（L14）。设置「偏好」中提供可选开关，默认关；依法需要时可开。",
    ],
  },
  {
    title: "免责",
    paragraphs: [
      "本页不是法律意见；客户权利义务以签署的《速影 Studio 软件许可及技术服务合同》为准。",
    ],
  },
];
