# 关于速影 · 文案草稿（未接线 UI）

> **状态**：草稿 · 待用户裁版权主体与隐私短文后，再进「设置 → 关于」  
> **约束**：接线属 App 可见文案 → 必须走 publish workflow；本文件单独合入**不**发包  
> 冲突时：`HARD_LOCKS` / `DEV_LOCK` > 本文

---

**速影 Studio**  
版本：{{APP_VERSION}}（例如 0.8.35）

本地智能混剪工作台：素材入库、成片生产、审片、发布物料与人在回路触达。

Copyright © {{COPYRIGHT_HOLDER}} {{YEAR}}. All rights reserved.  
软件本身为专有许可；第三方组件见「开源与第三方致谢」。

### 开源与第三方致谢

本产品使用 FFmpeg（GPL）、Ollama（MIT）、本地大模型（Apache-2.0）等组件。  
完整清单与许可证正文见应用内致谢，或离线交付 legal 组件中的  
`THIRD_PARTY_MANIFEST.json` / `THIRD_PARTY_NOTICES.zh-CN.md`。  
GPL/LGPL 组件的对应源码要约材料随离线 legal 组件提供。

图形表情：Twemoji（CC-BY-4.0）。字体：以 SIL OFL 等许可随包或系统字体为准。

### 语音合成提示

- **默认旁白（Edge）**：口播文本经微软 Edge 在线语音服务合成；非全离线能力。  
- **本地克隆（可选）**：依赖本机 F5 Runtime Kit；模型权重许可可能含非商业条款，商用须自行清权。

### 隐私与数据（摘要 · 待裁长文）

片库、成片、Chrome 发布配置与消息摘要默认保存在本机；不设默认公有云上传。  
可选 ntfy 通知仅发送脱敏摘要。详见交付合同与（若发布）《隐私说明》。

### 免责

本页不是法律意见；客户权利义务以签署的《速影 Studio 软件许可及技术服务合同》为准。
