# CLOSEOUT 真机执行看板（DEEP OPT.P5）

> **状态：关账 W4 已收口 · 2026-08-10 · 禁止再挂 NEED_HUMAN 进度**  
> 进度真相源：[`DEV_LOCK.md`](DEV_LOCK.md) · `CLOSE.W4`。  
> 本机自检证据：`~/Suying/logs/closeout-w4-20260810/`。

| 项 | DEV_LOCK | 状态 |
|----|----------|------|
| ACCEPTANCE A→F | GSP.6 … SEM.P5.H | **DONE · 2026-08-10** |
| GUI.SHIP | 序 105 | **DONE · 2026-08-10** |
| QUAL.NARR_B ready 抽验 | 序 197 | **DONE · 2026-08-10** · 用户确认过 |
| 客户机 config_truth 归档 | CLOSE.CONFIG_TRUTH / 序 204 | **DONE · 2026-08-10** |
| W4 全量自检 + decision | CLOSE.W4 / 序 205 | **DONE · 2026-08-10** |

## config_truth 归档结论（xlf-remote）

| 字段 | 值 |
|------|-----|
| 主机 | `xlf-remote`（路径按客户机现场） |
| 脚本根 | `/Applications/速影 Studio.app/Contents/Resources/runtime/studio` |
| seed | `…/Suying/incoming/remote-deploy/20260809T135758Z-37388-tmphYPo5POtkQ/customer-config` |
| 证据 | `/Users/xlf/Suying/logs/closeout-opt-20260810/config_truth_xlf-remote.{json,txt}` |
| ok | true（只读成功） |
| 词池 | live rev = seed rev = **4** |
| 规则 | id=16「产品介绍」rev=15 approved |
| data_root | `/Users/xlf/Suying/data` |
| drift | true · **仅** `video_lock`：live=`null`，seed 有完整 VIDEO_LOCK |
| 判定 | **接受该 diff · 不做 import** |
| 原因 | 运行时 `load_video_lock` = 默认 ← **工作区 `05-品牌/VIDEO_LOCK.json`** ← 可选 `profile_json.video_lock` 覆盖；`config_truth_diff` 只探针 profile 字段，**live=null 常正常**。强行 `--video-lock` import 会把 seed 写进 profile，非必需且可能与盘上品牌锁双写歧义。 |

**运维备忘：** 客户机没有 `~/QR/dev/速影`；对照一律用 App runtime 或 incoming deploy 的 `customer-config`。
