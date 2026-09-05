# 本地克隆 TTS（F5-TTS 声色包）

> Gate：**GVoiceClone** · 配套 `engine/pack/voice_clone.py` · `engine/pack/f5_kit.py` · `configs/voice_packs/`
>
> **HARD_LOCKS L19（2026-08-07 冻结）**：本机 F5 **就绪语义**与设置页试听链路已验收；**无用户当面授权禁止再改**就绪判定核心键、预览 API 与相关设置页实现。
>
> **交付拓扑（2026-08-07 起写死）**：**core 一体包 + App 外 F5 Runtime Kit**；clone flavor 仅应急。
>
> 冲突时：`DEV_LOCK.md` / `HARD_LOCKS.md` > 本文。权威索引见 [`README.md`](README.md)。

## 能力

- TTS `provider=clone`（别名：`f5` / `aunt`）
- 默认声色包：`configs/voice_packs/aunt_slow/`（慢速文学参考 ≈ 3.3 字/秒）
- 客户可覆盖：`<客户>/05-品牌/voice/{ref.wav,ref.txt,pack.json}` 或 `VIDEO_LOCK.voice`
- 设置页：音色引擎 edge|clone；「试听一句」→ `POST /voice/packs/preview`

## VIDEO_LOCK 示例

```json
"voice": {
  "provider": "clone",
  "voice_pack": "aunt_slow",
  "label": "阿姨慢速旁白",
  "chars_per_sec_zh": 3.3,
  "clone_speed": 1.0
}
```

全局默认仍为 Edge 晓晓；仅客户锁或 `settings.tts_provider=clone` 时启用。

## 双组件合同（稳定主路径）

| 组件 | 路径 | 升级频率 | core 覆盖安装 |
|------|------|----------|----------------|
| **App** | `/Applications/速影 Studio.app`（**`RUNTIME_FLAVOR=core`**） | 周更 / T2S | **整包替换** |
| **F5 Runtime Kit** | `~/Suying/runtime/f5_site_packages` + `f5_kit.manifest.json` | 罕发（ABI/栈升级） | **不碰** |

```text
~/Suying/runtime/
├── f5_site_packages/          # ~1.5G torch+f5 等
│   └── .suying_f5_overlay.json
└── f5_kit.manifest.json       # kit_rev / python_tag / sha

权重: ~/.cache/huggingface/hub/models--SWivid--F5-TTS*
     ~/.cache/huggingface/hub/models--charactr--vocos-mel-24khz*
```

- 引擎启动：`apply_f5_site_overlay()` 将 Kit 插到 `sys.path` 前端（**不改 App 树 → 无需为 overlay 重签**）。
- `clone_runtime_status`：`f5_source=overlay|bundle|missing`；运维字段 `f5_kit_rev` / `f5_kit_compat_ok`。
- **禁止**常态把 F5 merge 进 App（`diag_fix … --fix` = **LEGACY emergency only**，下次 core 必丢）。

`RUNTIME_FLAVOR=clone`：**仅**单 U 盘全离线应急一体机，**不是** T2S 周更默认。

## 首装流程（需 clone）

顺序写死：

1. 主盘就绪（**L18**）：`~/Suying/data` 等  
2. 装 **core** App：`安装到-应用程序.sh`  
3. 装 **F5 Kit**（构建机先产出，见下）：

```bash
# 构建机：从含 f5 的 stage/App 导出并打包（一次性，不交付 fat App）
python3 scripts/materialize_f5_overlay.py --from-app "/path/含f5.app" --clean
./scripts/package-f5-runtime-kit.sh --from-overlay ~/Suying/runtime/f5_site_packages

# 目标机：
./scripts/install-f5-runtime-kit.sh --app "/Applications/速影 Studio.app" \
  ~/Suying/releases/f5-runtime/kits/速影-f5-runtime-kit-….tar.gz
```

4. 权重：`materialize_clone_tts_cache.py --import-from …` 或联网首次推理；离线交付必须先 import  
5. 烟测：

```bash
python3 scripts/materialize_f5_overlay.py --check
./scripts/verify_f5_overlay_post_install.sh --require-overlay-source
# 引擎 up：GET /jobs/pipeline → clone_runtime.f5_source=overlay · clone_available=true
# 设置页试听一句（L19）
```

客户机：`deploy-remote` 自动选 **core** App；`VIDEO_LOCK=clone` 时 **必须** `--f5-kit`（本机不堆 kit 历史；勿依赖长期留在 `~/Suying/releases/f5-runtime/kits/`）。`FORCE_CLONE_FLAVOR_APP=1` 才打 fat clone App。

## 周更 / 推 T2S / 覆盖安装

```bash
# 只动 App —— 不重做 Kit（推送后本机 releases 默认清空）
RUNTIME_FLAVOR=core ./scripts/release-to-t2s.sh
./scripts/verify_f5_overlay_post_install.sh --require-overlay-source
```

| 允许 | 禁止 |
|------|------|
| 覆盖 `/Applications/速影 Studio.app` | 删除 `~/Suying/runtime/f5_*` |
| 覆盖后期望 `f5_source=overlay` | 用 diag_fix merge 回 App 当常态 |
| Kit 独立升 rev；推 T2S 后删本机 kit | 把 1.5G F5 塞进每次 core ZIP；本机堆 kit 历史 |

### Kit 独立发 T2S（分轨）

```text
T2S: 速影/更新包/f5-runtime/
  latest-kit.json
  kits/速影-f5-runtime-kit-<python_tag>-r<rev>.tar.gz
```

```bash
./scripts/package-f5-runtime-kit.sh
python3 scripts/publish_f5_kit_repo.py --package ~/Suying/releases/f5-runtime/kits/….tar.gz
# 默认推送成功后删除本机 kit 与同名 .meta.json（KEEP_LOCAL=1 可保留）
```

Kit 何时重建：standalone CPython 大版本变 / torch·F5 栈主动升级 / 损坏修复。

## 运行时就绪（L19 语义 · 保留）

| 项 | 要求 |
|----|------|
| `clone_available` / `f5_tts_importable` | 能 `import f5_tts`（**overlay 或** 应急 bundle） |
| 一体包默认 | **core 不含 F5**；可用 = Kit 已装 |
| 权重 | HF hub F5-TTS + Vocos（`weights_ready`） |
| fail-closed | 无 f5 时 `provider=clone` 不静默回落 Edge |

## 试听合同（L19 · 已验收 · 勿改行为）

| 项 | 写死 |
|----|------|
| API | `POST /voice/packs/preview` → **单轨 `preview.wav`** + 真实 `audio_path` |
| 出声 | macOS **`afplay`** |
| 失败 | 4xx，禁止空路径 200 |

## 事故表

| 现象 | 处置 |
|------|------|
| 刚装 core 后「F5 未就绪」 | 装 Kit，**不要** merge App |
| 覆盖后丢失 | 旧热补被清；重装 Kit |
| python_tag 不匹配 | 重 materialize 对应 App cpython 的 Kit |
| OFFLINE 无权重 | `materialize_clone_tts_cache --import-from` |

## 脚本索引

| 脚本 | 用途 |
|------|------|
| `materialize_f5_overlay.py` | 从 App/stage 导出 overlay + manifest |
| `package-f5-runtime-kit.sh` | 打 tar.gz → 暂存 `~/Suying/releases/f5-runtime/kits/`（推 T2S 后默认删除） |
| `install-f5-runtime-kit.sh` | 原子装到本机 runtime |
| `verify_f5_overlay_post_install.sh` | core 覆盖后验收 |
| `publish_f5_kit_repo.py` | 推 T2S f5-runtime 轨 |
| `smoke_f5_kit.py` | 合同冒烟 |
| `materialize_clone_tts_cache.py` | 权重 export/import |

## 许可注意

- 参考音须本人/客户授权  
- F5-TTS 权重 **CC-BY-NC**：商用再分发模型须清权  

## 勿做

- core 包冒充 clone 已就绪（无 Kit）  
- 往 App site-packages 热补当交付  
- L19 试听合同行为漂移  
- mock / say 冒充 clone  

## 变更

| 日期 | 事件 |
|------|------|
| 2026-07-31 | GVoiceClone 开闸 |
| 2026-08-07 | 就绪+试听验收 → **L19** |
| 2026-08-07 | **core + App 外 F5 Kit** 稳定交付合同落地 |
