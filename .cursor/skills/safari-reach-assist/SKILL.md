---
name: safari-reach-assist
description: >-
  速影 Safari 单账号发布（半自动或尽量全自动）：读 publish_pack/队列，Safari 打开创作者中心，
  JS 点击发布入口；上传文件对话框需辅助选文件；填标题正文；验证码停人；发完记账。
  多号串行：一书签一切号，用户确认后重复。不用 Cursor 浏览器。Use when user says Safari 发布、单账号全自动、全部在 Safari。
---

# Safari 单账号发布

默认路径：**全部在 Safari**（不用 Cursor 内置浏览器）。

## 目标流程（单号全自动上限）

1. 读物料（`safari_reach_assist.py --accept-risk --export-json …`）
2. Safari 打开/聚焦创作者中心（已登录）
3. `do JavaScript` 点击「发布视频」→ `/content/upload`
4. 点「上传视频」→ **系统文件框**
5. 选视频：使用 publish_pack 中的 `video.mp4`（剪贴板粘贴成片目录，禁止默认 `/tmp`）
6. 上传完成后填标题/正文（JS 或剪贴板）
7. 无验证码则点「发布」；有验证 → 停，喊人
8. `--mark-published`
9. 提示下一书签；用户切号后回「下一个」→ 重复 1–8

## 硬限制

- 同一 Safari 配置内书签**不会**自动换 Cookie；切号靠用户点书签/换窗口。
- 系统文件框依赖 **辅助功能权限**（系统设置 → 隐私与安全性 → 辅助功能，勾选 Terminal/Cursor）。
- 禁止：Cookie 池、矩阵并行、反检测、自动过滑块/打码。

## 以后全自动（必备一次）

系统设置 → 隐私与安全性 → **辅助功能** → 打开开关：

- **Cursor**
- （若用终端跑脚本）**Terminal** / **iTerm**

未授权时：只能停在「上传」请人选文件。  
已授权后：Agent 可操作文件框选 **publish_pack/video.mp4**（剪贴板粘贴成片目录，禁止默认 `/tmp`），再填表、点发布（验证码仍人过）。

## 位置权限弹窗（必须自动点「允许」）

当 Safari 出现：

> “creator.douyin.com” 想使用你当前的位置。

**一律自动点击「允许」**（不要点「不允许」）。  
可选勾选「在一天内记住我的决定」。  
出现时机常见于填表/点位置相关控件之后；每步截图后若发现该弹窗，先点允许再继续。

执行：

```bash
osascript scripts/safari_helpers/dismiss_location_allow.applescript
```
