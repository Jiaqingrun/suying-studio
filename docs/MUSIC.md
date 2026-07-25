# 速影 BGM 曲库说明（全局）

权威调用说明；客户盘 `04-音乐/README.md` 应与本文件保持一致。

## 如何被引擎调用

1. **优先**客户目录 `…/<客户>/04-音乐/*.mp3`（仅根目录，**不含子文件夹**）
2. **回退**全局 `~/Suying/music/*.mp3`
3. 跳过 `placeholder-*.mp3`
4. 按任务 `seed` 从「优先池」里取曲；优先池 = 文件名命中轻快/轻柔关键词的曲目

**混音默认：** 去掉素材原声，口播主轨 + BGM 床（`ambient_gain=0`，`bgm_bed_gain≈0.2`）。

**手动指定某首（运维/脚本）：** 把目标 mp3 **单独**放进客户 `04-音乐/` 根目录并临时移走其它曲，或改文件名包含优先关键词后重渲。成片 sidecar `meta.bgm_name` / `meta.music_credit` 可核对实际抽到哪首。

## 来源与合规

| 前缀 | 来源 | 许可 | 署名模板 |
|------|------|------|----------|
| `bensound-*.mp3` | [Bensound](https://www.bensound.com/) | 免费版需署名 | `Music: <曲名> — Bensound.com` |
| `km-*.mp3` | Kevin MacLeod / [Incompetech](https://incompetech.com/) | **CC BY 3.0** | `Music: <曲名> by Kevin MacLeod (incompetech.com) · CC BY 3.0` |

- 可以：短视频成片内嵌 BGM  
- 不要：单独再分发/上架售卖 mp3；不要下抖音榜单热歌  
- 引擎对 `km-` / `bensound-` 会自动写入 `music_credit`（发布文案可带 🎵）

## 曲风 → 文件名（调用表）

文件名即调用名（不含扩展名）。`km-` 为轻柔欢快向（已人工筛过）；`bensound-` 偏节奏/日更。

### 轻松日更 / 轻柔欢快（优先 `km-`）

| 调用名 | 气质 |
|--------|------|
| `km-carefree` | 轻柔明亮 |
| `km-easy-lemon` | 轻快柠檬感 |
| `km-wallpaper` | 软铺底 |
| `km-wholesome` | 温暖正向 |
| `km-rainbows` | 轻快彩色 |
| `km-vivacity` | 活泼 |
| `km-hyperfun` | 轻快趣味 |
| `km-upbeat-forever` | 轻上扬 |
| `km-digital-lemonade` | 清爽电子轻快 |
| `km-whimsy-groove` | 轻律动 |
| `km-fluffing-a-duck` | 俏皮短视频感 |
| `km-happy-alley` / `km-happy-boy-theme` | 欢快小主题 |
| `km-porch-swing-days-faster` / `…-slower` | 门廊轻松 |
| `km-clear-waters` / `km-beauty-flow` / `km-eternal-hope` | 柔和铺底 |
| `km-beachfront-celebration` / `km-almost-new` | 轻庆祝 / 清新 |
| `km-monkeys-spinning-monkeys` / `km-merry-go` | 俏皮欢快 |
| `bensound-ukulele` / `buddy` / `sunny` / `cute` | 日更轻快（Bensound） |

### 稳镜产品 / 门店

`bensound-creativeminds` · `moose` · `happyrock` · `summer` · `km-carefree` · `km-easy-lemon` · `km-life-of-riley` · `km-lobby-time`

### 快切发货 / 装车

`bensound-energy` · `popdance` · `dance` · `dubstep` · `hey` · `km-hyperfun` · `km-upbeat-forever` · `km-faster-does-it` · `km-run-amok`

### 其它已入库 `km-`（可抽，偏个性）

`acidjazz` · `bossa-antigua` · `cattails` · `daily-beetle` · `feelin-good` · `folk-round` · `funkorama` · `gymnopedie-no-1` · `ice-flow` · `jazz-brunch` · `local-forecast` · `mellowtron` · `pinball-spring` · `samba-isobel` · `smooth-lovin` · `sneaky-snitch` · `suonatore-di-liuto` · `thatched-villagers` · `ultralounge` · `windswept` · `your-call`

## Agent / 运维清单

- 新曲必须放在 **`04-音乐/` 或 `~/Suying/music/` 根目录**（不要放子文件夹）
- 文件名用 `km-<slug>.mp3` 或 `bensound-<slug>.mp3`，便于署名与抽曲
- 改曲库后**无需重启引擎**（每次渲染重新扫目录）
- 核对：成片旁 `.json` → `meta.bgm_name` / `meta.music_credit`
