# 速影 Studio · 第三方软件致谢（人读版）

> 机器权威清单：[`../THIRD_PARTY_MANIFEST.json`](../THIRD_PARTY_MANIFEST.json)  
> 交付门禁：[`DELIVERY_NOTICE.zh-CN.md`](DELIVERY_NOTICE.zh-CN.md) · [`../OFFLINE_DEPOT_LEGAL_DELIVERY.zh-CN.md`](../OFFLINE_DEPOT_LEGAL_DELIVERY.zh-CN.md)  
> 冲突时：`DEV_LOCK.md` / `HARD_LOCKS.md` > 本文。  
> **不是法律意见。**

对齐产品版本：**0.8.35**（随 Manifest 更新）。

## 1. 现行交付闭包（须进 legal 组件）

| 组件 | 版本（Manifest） | SPDX / 许可 | 许可证正文 | 源码要约 |
|------|------------------|-------------|------------|----------|
| FFmpeg | 8.1.2_1 | GPL-3.0-or-later | `licenses/ffmpeg-8.1.2_1/` | `source-offer/ffmpeg-8.1.2.tar.xz` |
| x264 | r3222 | GPL-2.0-or-later | `licenses/x264-r3222/` | `source-offer/x264-*.tar.gz` |
| x265 | 4.2 | GPL-2.0-or-later | `licenses/x265-4.2/` | `source-offer/x265_4.2.tar.gz` |
| LAME | 3.101 | LGPL-2.0-or-later | `licenses/lame-3.101/` | `source-offer/lame-3.101.tar.gz` |
| mpg123 | 1.33.6 | LGPL-2.1-only | `licenses/mpg123-1.33.6/` | `source-offer/mpg123-1.33.6.tar.bz2` |
| dav1d / libvpx / libvmaf / Opus / SVT-AV1 / OpenSSL | 见 Manifest | BSD / Apache-2.0 等 | `licenses/*` | 一般不强制 |
| Ollama | 0.24.0 | MIT | `licenses/ollama-0.24.0/` | — |
| 内嵌 CPython + site-packages | 3.12 + dist-info | 混合 | 构建生成 `python-runtime/THIRD_PARTY_LICENSES.txt` | 见该目录 README |
| nomic-embed-text | latest（manifest SHA 见 Manifest） | Apache-2.0 | `model-licenses/nomic-embed-text-latest.LICENSE.txt` | — |
| qwen3.5:9b | 9b（manifest SHA 见 Manifest） | Apache-2.0 | `model-licenses/qwen3.5-9b.LICENSE.txt` | — |

Homebrew formula / receipt：`homebrew-evidence/`。摘要：`MATERIAL_SHA256.txt`。Provenance：`PROVENANCE.md`。

## 2. 运行时依赖（须披露，待扩 Manifest）

| 组件 | 角色 | 条款要点 |
|------|------|----------|
| edge-tts（默认旁白） | 联网调用微软 Edge 在线语音 | **非全离线**；文本出站；服务条款由使用方遵守（销售合同 §14.5） |
| F5-TTS + Vocos（可选 clone / F5 Kit） | 本地克隆旁白 | 权重多为 **CC-BY-NC**；商用再分发须清权（`VOICE_CLONE.md`） |
| Twemoji | 字幕 emoji 贴图 | CC-BY-4.0；`configs/fx_assets/licenses/CC-BY-4.0-Twemoji.txt` |
| 字体（Noto / Source Han / 等） | 烧录字幕 | SIL OFL 1.1；见 `FX_ASSET_WHITELIST.md` |
| npm：React / Tauri API / lucide-react 等 | 桌面 UI | 多为 MIT；发版前补 SPDX 表 |
| crates：tauri / serde / ring 等 | 桌面壳 | 多为 MIT/Apache-2.0；发版前补 SPDX 表 |

## 3. 不进入现行交付

以下材料仅历史审计，**不得**装入客户 App 或 legal 组件：

- Remotion 4.0.484  
- OpenMontage（AGPL 取证）  
- Cursor SDK 1.0.26  

详见 `PROVENANCE.md`「已下线组件」。

## 4. 音乐与客户内容

- 预置/推荐 BGM：`docs/MUSIC.md`（Bensound 署名 · Kevin MacLeod CC BY 3.0）  
- 客户实拍、词包、参考人声、自备音乐：由客户保证授权（销售合同第十三条、十四条）

## 5. App 关于页

人读文案草稿（**未接线 UI**）：[`APP_ABOUT_COPY_DRAFT.zh-CN.md`](APP_ABOUT_COPY_DRAFT.zh-CN.md)。  
接线属可见文案变更，须走 publish workflow。
