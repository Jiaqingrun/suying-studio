---
name: vision-reach-publish
description: >-
  速影 G5.V 视觉全自动发布（本人单账号、风控自负）：用 Cursor IDE Browser 快照+截图
  （或 Safari 截屏）识别创作者页面并自动点击/输入/点发布；遇登录验证码必须停并喊人。
  Use when the user asks for 视觉全自动, vision publish, 自动点发布, or G5.V.
---

# 视觉全自动发布（G5.V）

权威：`docs/REACH_NON_GOALS.md` · `docs/DEV_LOCK.md`（锁变更五）。

## 硬规则

1. 仅 **本人单账号**；禁止矩阵/养号/反检测/打码。
2. 出现 **登录、扫码、验证码、滑块、风控** → 立即停，中文告知用户，等「验证完成」。
3. 连续 **4 次** 同类操作失败 → 停，报告阻塞，改回半自动/粘贴卡。
4. 不承诺过检；误发风险由用户自负。
5. **文件选择**：若无法把本地 `video.mp4` 注入 `<input type=file>` → 停，请用户选一次文件，然后继续全自动填表与点发布。

## 路径 A（推荐）：Cursor IDE Browser

Cookie **不等于** Safari。用户须在 IDE Browser 登录抖音创作者。

### 准备物料

```bash
cd ~/QR/dev/montage-studio
python3 scripts/safari_reach_assist.py --accept-risk --queue-id 1 --no-open --clipboard none --export-json /tmp/reach_payload.json
```

读 `/tmp/reach_payload.json` 的 `title` / `body` / `video` / `url`。

### 浏览器循环

1. `browser_tabs` list → 若无标签则 `browser_navigate` 到 `url`（`position: "active"` 便于用户看）
2. `browser_lock` lock
3. 循环直到成功或阻塞（每步都要新证据）：
   - `browser_snapshot` 找「上传 / 发布 / 标题 / 描述」等
   - 需要肉眼确认时 `browser_take_screenshot`
   - `browser_click` / `browser_fill` / `browser_type` / `browser_press_key`
   - 文案用 payload 的 title、body
4. 点「发布」前再 snapshot+screenshot 确认无验证弹层
5. 成功后 `--mark-published`；`browser_lock` unlock

### 验证阻塞话术

```text
⚠️ G5.V 停下：需要你在 Cursor 浏览器里完成登录/验证。
完成后回复「验证完成」，我继续自动填表与发布。
```

## 路径 B：Safari 已登录

IDE Browser 登不了或不想重登时：

1. `safari_reach_assist.py --accept-risk` 打开 Safari + Finder 揭示视频
2. `screencapture` 截当前屏并看图
3. 用 AppleScript/System Events 点击与键入
4. 同样：验证停人；文件框请人选手动

比路径 A 更脆，优先 A。

## 成功定义

- 平台显示发布成功或作品列表出现新稿
- 触达队列 `published`
- 若有 `ship_trial_s1.json`，更新 `human_click_publish: true`

## 禁止

打码、指纹伪装、多开、自动过滑块、连发多账号。
