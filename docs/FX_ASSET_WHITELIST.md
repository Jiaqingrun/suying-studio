# 速影 · 免费可商用特效/字体白名单（GRuleVisualLab）

> **效力：分锁。** 状态只写 [`DEV_LOCK.md`](DEV_LOCK.md) §E。  
> **不是法律意见。** 入库须附许可证原文；聚合站 / 不明许可 **永不收录**。  
> 冲突时：`DEV_LOCK` / `HARD_LOCKS` > 本文 > [`VISUAL_DYNAMIC_OPTIMIZATION.md`](VISUAL_DYNAMIC_OPTIMIZATION.md)。

## 分级

| 级 | 策略 |
|----|------|
| **A** | 可进包 / 可进规则枚举（官方源 + 可再分发） |
| **B** | 默认不进一体包（如 OpenMoji CC BY-SA） |
| **C** | 拒收 |

机器可读副本：[`configs/fx_assets/manifest.json`](../configs/fx_assets/manifest.json)。

## Tier A · 转场（程序化 · 无第三方素材文件）

| id | 类型 | 官方依据 | SPDX / 许可 | 再分发 | 署名 | 横竖 |
|----|------|----------|-------------|--------|------|------|
| `clip_transition=none` | 硬切（默认） | 自研 | — | n/a | 否 | 共用 |
| `xfade:*` | FFmpeg xfade 全表 | 交付 FFmpeg 闭包 | 随 FFmpeg legal | 已在闭包 | 见 FFmpeg NOTICE | 共用；时长默认 ≤0.5s |

本机校准：`ffmpeg -h filter=xfade`。未编译进的 transition 不得宣称收录。

## Tier A · 字体（SIL OFL 1.1 · 附 OFL.txt）

| id | 官方源 | 再分发 | 署名 | 备注 |
|----|--------|--------|------|------|
| `noto_sans_sc` | https://github.com/notofonts/noto-cjk · Google Fonts Noto Sans SC | 可（附 OFL） | OFL 要求保留许可文件 | 优先子集字重 |
| `noto_serif_sc` | 同上 Noto Serif SC | 可 | 同上 | 可选 |
| `source_han_sans_sc` | https://github.com/adobe-fonts/source-han-sans | 可 | 保留名 Source | 体积大，Subset |
| `source_han_serif_sc` | https://github.com/adobe-fonts/source-han-serif | 可 | 同上 | 可选 |
| `zcool_kuaile` | Google Fonts ZCOOL KuaiLe | 可 | OFL | 活泼标题 |
| `lxgw_wenkai` | https://github.com/lxgw/LxgwWenKai | 可 | OFL | 文楷 |
| `inter` | https://github.com/rsms/inter | 可 | OFL | 拉丁/数字 |
| `system` / 本机已装 | 用户机器 | 不随包拷贝系统字体文件 | — | 扫描列举；用户自担 |

拉取脚本：`scripts/fetch_fx_fonts.sh` → `configs/fx_assets/fonts/`（大文件默认 gitignore，发版按需打入）。

## Tier A · 表情 / 贴图

| id | 官方源 | SPDX | 再分发 | 署名 |
|----|--------|------|--------|------|
| `twemoji` | https://github.com/twitter/twemoji | CC-BY-4.0（图形） | 已用 | 设置/法律页 |
| `noto_emoji` | https://github.com/googlefonts/noto-emoji | 字体 OFL；图像多为 Apache-2.0 | 可（分路径记许可） | Apache NOTICE |
| `user_png` | 用户自有 | 用户自负 | 不进官方分发 | — |

**B · 不进包：** OpenMoji（CC-BY-SA-4.0）。

## Tier A · 蒙版

| id | 来源 | SPDX | 再分发 | 备注 |
|----|------|------|--------|------|
| `geom_*` | 自研几何（暗角/渐变/条带） | 自有 | n/a | 优先 |
| `oga_sbs_anim_trans` | OpenGameArt Animated Transitions Pack | CC0-1.0 | 可 | 可选；缩放适配横竖 |
| `user_mask` | 用户 PNG | 用户自负 | 否 | 多层见 schema `mask_layers` |

## Tier A · LUT

| id | 策略 |
|----|------|
| `color_lut=off\|light` | 程序 `eq`，无第三方文件（保留） |
| 第三方 `.cube` | **仅**作者明示 CC0 且许可证可核验的逐条；禁止营销站 Mega Pack 盲收 |

## C · 拒收（示例）

FreeGFX 转载站、无 SPDX 的「免费商用」LUT 站、禁止再分发进软件的素材、剪映/PR 未授权模板、OpenMontage/Remotion 运行时、数字人假脸。

## 运营合同

- 字段默认关；`mask_layers` 默认 `[]`；日更不引导重特效。  
- 精品可开；最多 **8** 层蒙版；转场默认时长 **0.4s**（钳制 ≤0.8s）。  
- 不松 READY / L15 / 旁白对齐 / L17–L20。

## 入库门禁（legal-gate）

每项必须具备：`id`、类型、官方 URL、许可证全文路径、是否允许再分发进 App、署名位置、横竖策略。缺一不可进 `manifest.json`。
