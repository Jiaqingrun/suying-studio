# 速影 · 最终开发标准（通用剪辑发布工作室）

> **状态：2026-07-24 冻结；2026-07-28 按已授权 Gate 增补系统暂停与安装硬锁**
> **适用：** 产品、App、引擎、配置、测试、交付全链路。  
> **前提：** 速影是**多客户通用产品**；「北京始峰伟业」只是**首个样板客户（fixture）**，不是产品本体。  
> 产品路线见 [`PRODUCT_PLAN.md`](PRODUCT_PLAN.md)；质量冲刺见 [`V8_QUALITY.md`](V8_QUALITY.md)。

> 冲突时：`DEV_LOCK.md` / `HARD_LOCKS.md` > 本文。权威索引见 [`README.md`](README.md)。
---

## 0. 铁律（先背这 8 条）

1. **引擎零业务硬编码**：行业词、钩子文案、主题规则、品牌色、Logo、禁词，全部进「客户包 / 行业包」配置，禁止写死在 `engine/` 主路径。  
2. **一切生产以 `customer_id` 为边界**：素材、任务、日历、冷却、成片、审片、报表、发布队列，禁止串客户。  
3. **App 只编排、引擎只干活**：UI 不跑 FFmpeg、不做抽帧、不写业务 SQL；引擎不依赖 Tauri。  
4. **配置与内容分离**：代码 = 能力；`configs/` + 客户目录 = 差异化。  
5. **同步区 ≠ 工作区**：客户可见物进同步；db/cache/render/cookies/密钥永不进同步树。  
6. **新客户 15 分钟可开工**：向导建客户 → 指路径 → 导入词包/行业包 → 扫库 → dry-run，无需改代码。  
7. **样板客户可删**：删掉始峰配置与脚本，产品仍完整可装、可测、可卖。  
8. **不做反检测卖点**：Reach 层人在回路；禁止以绕开平台风控为验收标准。
9. **自动闭环必须可恢复可审计**：生产、READY_GATE、出包、排期、发布、核验与消息通知以持久化 occurrence 串联；每次窗口随机保存独立 seed，重启不得重抽或重复副作用。
10. **即时与定时设置隔离、内容共享 ready 池**：即时批次不得改写定时 schedule/trigger/seed/窗口；内容在执行时现挑共享 `ready` 池（无提前预留）；全部账号仍共享机器级单槽。
11. **日志统一、丰富且脱敏**：关键步骤写 append-only 结构化日志，带客户域和关联 ID；Cookie、token、验证码、完整正文不得入日志。

---

## 1. 产品本体 vs 客户实例

| 层级 | 是什么 | 例子 |
|------|--------|------|
| **产品（Product）** | 速影 Studio / Pack / Reach | 混剪、质检、物料包、半自动发布 |
| **行业包（Industry Pack）** | 可复用的主题/钩子/模板绑定预设 | `building-supply` 建材仓配、`beauty` 美业、`food` 餐饮 |
| **客户（Customer / Tenant）** | 一台机器上的隔离工作区 | 始峰、演示客户、未来任意公司 |
| **品牌覆写（Brand Override）** | 单客户的色、Logo、禁词、语气 | `configs/customers/<name>/brand/` |

**开发标准：** 新功能默认做在「产品」层；只有证明 ≥2 个客户需要同一套文案/主题时，才抽成「行业包」；单客户差异只进客户目录。

始峰相关脚本（`bootstrap_shifeng.py`、`convert_shifeng_*`）定位为 **samples/fixtures**，不得成为唯一启动路径。通用入口必须是：App 向导 / `POST /customers` / `configs/samples/`。

---

## 2. 目标架构（目标态）

```text
┌─────────────────────────────────────────────────────────┐
│  apps/desktop  速影 App（Tauri）                          │
│  总览 · 生产 · 素材 · 审片 · 物料 · 触达 · 运维            │
│  只调 HTTP API；本机启停引擎；选路径；展示状态             │
└──────────────────────────┬──────────────────────────────┘
                           │ REST localhost
┌──────────────────────────▼──────────────────────────────┐
│  engine/api          边界：鉴权无（本机单用户）· 校验 · DTO │
│    归属（新路由禁止入 app.py 主体）：                         │
│    job_routes · catalog_routes · reach_* · content_* ·     │
│    system_routes · maintenance · library_review · …        │
│  engine/catalog      客户域 · 词包 · 日历 · 向量            │
│  engine/ingest       扫描 · normalize · proxy · cliplet   │
│  engine/template     模板引擎（读配置，不读客户名）         │
│  engine/render       FFmpeg 成片                          │
│  engine/qc           质检门闩                              │
│  engine/jobs         队列 · Worker · 熔断                  │
│  engine/export       sidecar · publish_pack · CSV         │
│  engine/pack         （Phase 2）文案适配 · TTS · 字幕       │
│  engine/reach        （Phase 4）发布队列 · 消息提醒         │
│  engine/ops          磁盘 · 调度 · 同步适配器               │
└──────────────────────────┬──────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────┐
│  数据平面                                                  │
│  工作区（不同步）：db / cache / render / logs / secrets     │
│  客户同步区：01-片库 / 02-成片 / 03-词池 / 04-音乐 / 05-品牌 │
│  引导：~/Suying/data/settings.json → 工作区指针            │
└──────────────────────────────────────────────────────────┘
```

**模块依赖方向（只允许向下）：**

```text
api → jobs/pack/reach/ops/catalog/ingest/template/render/qc/export
jobs → template, render, qc, export, catalog
pack → catalog, export（不反向依赖 reach）
reach → export/pack 产出物（不进 render 内核）
template/render/qc  ← 不得 import apps、不得读「客户中文名」分支
```

---

## 3. 多客户与数据标准

### 3.1 租户字段

- 凡属生产数据的表必须有 `customer_id`（或经 Asset 等可 join 到客户）。  
- API 默认作用域 = `settings.active_customer` → `require_active_customer()`。  
- 禁止「全局最近标题/钩子」跨客户冷却。

### 3.2 客户目录合同（标准布局）

每个客户在同步区（或本机等价路径）必须可解析为：

```text
<customer_root>/
  01-片库/          # library_root（可多根，见 library_roots）
  02-成片/          # output_root → ready|review|failed|packs
  03-词池/          # keyword pack 文件
  04-音乐/          # 可选 BGM（无则回退全局 music_root）；见 docs/MUSIC.md
  05-品牌/          # logo、字体、style_lock；封面模板/（每客户固定）
    封面模板/        # index.json + tpl_<id>/ 各平台槽位图（权威存放）
```

引擎只认 **路径字段**，不认「始峰」「徐玲飞」等业务名。人员子目录是客户片库内部约定，产品不解析人名语义。

封面模板不得混放在工作区全局 `db/cover_templates/`（仅作迁移源）；读写一律走当前客户的 `05-品牌/封面模板/`。

### 3.3 Customer.profile_json（推荐 schema）

```json
{
  "industry_pack": "building-supply",
  "locale": "zh-CN",
  "brand": {
    "display_name": "",
    "primary_color": "#E10600",
    "logo_enabled": true,
    "logo_path": "",
    "logo_position": "bottom_right",
    "logo_max_width": 160,
    "logo_margin": 36,
    "title_layout": "dual_chip"
  },
  "compliance": {
    "banned_terms": [],
    "require_disclaimer": false
  },
  "publish": {
    "platforms": ["douyin", "channels", "xhs", "wechat_mp"],
    "default_lang": "zh"
  },
  "audio": {
    "keep_source_audio": false,
    "bgm_gain": 0.45
  }
}
```

未知字段必须忽略（前向兼容），不得因缺字段崩溃。

### 3.4 默认客户

- **禁止** `DEFAULT_CUSTOMER = "北京始峰伟业"` 作为产品默认。  
- 允许：空 → 强制向导；或 `演示客户` 空片库 fixture。  
- 样板数据只存在于 `configs/customers/<样板名>/` 与 `scripts/fixtures/`。

---

## 4. 配置与行业包标准

### 4.1 仓库内配置树

```text
configs/
  samples/                    # 通用示例（随安装走）
    sample-customer.json
    template-default.json
    industry/
      building-supply/        # 从始峰经验提炼的行业包（去品牌化）
        hooks.json
        theme_rules.json
        template_bindings.json
        keyword-pack.sample.json
      _blank/                 # 空包：仅 default 主题
  customers/                  # 可选：开发机上的客户覆写（勿当唯一真相）
    <customer_name>/
      keyword-pack.json
      brand/
```

### 4.2 运行时加载顺序（后者覆盖前者）

```text
内置最小默认（代码内极简 fallback）
  → industry_pack（若 profile 指定）
  → 客户 keyword-pack / brand / hooks
  → 任务级覆盖（API 入参）
```

### 4.3 配置化内容（已达标 / 仍可增强）

| 现况（问题） | 标准 |
|--------------|------|
| `hooks.py` / theme 规则 | ✅ 已迁入 `configs/samples/industry/*/pack.json`，经 `industry_pack.py` 加载 |
| 标题样式写在 TemplateDefinition 默认值 | 允许 brand profile 覆写色/字号/layout |

节奏模板（`default-vertical` / `fast-ship` / `stable-product`）属于**产品能力**，可留代码；**主题→模板映射**属于行业包。

### 4.4 正式备份（T2S）

行业包 / 起步种子 / 客户覆写的极空间正式备份见 [`INDUSTRY_PACK_BACKUP_ZSPACE.md`](INDUSTRY_PACK_BACKUP_ZSPACE.md)（目录「速影/行业包备份」；`current` + `releases` 基线；与主仓备份、更新包分离）。统一根见 [`T2S_PERSONAL_ROOT.md`](T2S_PERSONAL_ROOT.md)。编辑仍以本仓库 `configs/` 为真相源。

---

## 5. App 开发标准

### 5.1 信息架构（目标 Tab · GUI）

| Tab | 职责 | 是否客户相关 |
|-----|------|----------------|
| 总览 | 今日摘要、产线步骤、异常与下一步 | 当前客户 |
| 生产 | 素材扫描、横/竖画幅、任务、日历 | 当前客户 |
| 规则 | 横/竖独立混剪规则、标题、字幕、语言、音色、节奏与质量预览 | 当前客户 |
| 审片 | 预览、筛选、通过/打回/重渲、批量修复 | 当前客户 |
| 发布 | 表达设置、物料包、发布台、封面与触达队列 | 当前客户 |
| 消息 | 多平台本人账号只读摘要、账号巡检状态、跳官方页人工回复 | 当前客户 |
| 数据中心 | 只读统计：产能/质量/发布/词库/审计 | 当前客户 |
| 运维 | 设备服务、Ollama、横/竖向量、备份、T2S/同步/更新、路径健康、缓存、高级功能与日志 | 机器级 |
| 设置 | 客户/品牌/账号/通知/常规路径/已发布视频保留/外观 | 客户级 |

业务主线：`总览 → 生产 → 规则（按需）→ 审片 → 发布 → 总览`。商业离线版不内嵌第三方云代理助手。

顶栏**永久**展示：当前客户名、路径健康、引擎状态。切换客户必须清空列表缓存并重新拉取。

### 5.2 UI 原则

- 文案用「客户 / 片库 / 成片」，不用「始峰 / 徐玲飞」。
- 向导步骤固定：客户名 → 片库 → 成片 → 词池 →（可选）行业包 → 是否开向量化。
- 按 Tab 拆页面组件；**状态仍以 API 为准**，不在前端持久化业务库。
- 所有破坏性操作（打回、清缓存、删客户）二次确认。
- M 系列 MacBook Retina：用密度分档 + 重排提高一屏信息量；禁止整页 `zoom`/`transform: scale()`；紧凑档正文 ≥13px、主按钮高度 ≥32px。
- 布局密度：自动 / 舒适 / 紧凑；自动档按逻辑视口 `1280` / `1440` / `1680` 跳档。

### 5.2.1 语言下拉浮层（冻结 · 2026-08-07 · HARD_LOCKS L17）

规则页「声音与语言」的旁白/字幕语言选择器（`LangCombobox`）已验收修好，**默认禁止再改动**。

| 写死 | 说明 |
|------|------|
| **实现** | `apps/desktop/src/shell/LangCombobox.tsx`：`createPortal` → `document.body` + `position: fixed` 按 trigger 视口定位；视口避让与列表 `max-height` 内滚动。配套 CSS 仅 `.lang-combo*`（`z-index: 1500`）。 |
| **禁止** | 改回卡片内 `absolute` 弹层；靠去掉 `.rule-card` / `.page-section` 等祖先 `overflow`「治裁切」；无用户明确授权地「优化/重构/重写」本组件及相关 CSS。 |
| **验收** | 一体包前端嵌在 `appsdesktop` 二进制内。改源码 ≠ 改 `/Applications/速影 Studio.app`。宣称修好须：`npm run build` + 重建/覆盖 App（或 `tauri dev` 热链），并在真机打开语言列表确认不被卡片裁切。仅 `tsc` 通过不算 App 侧交付完成。 |

### 5.2.2 本地克隆旁白与试听（冻结 · 2026-08-07 · HARD_LOCKS L19）

设置页本地克隆 TTS（F5）就绪与「试听一句」已验收，**默认禁止再改动**核心语义。细则权威：[`VOICE_CLONE.md`](VOICE_CLONE.md)。

| 写死 | 说明 |
|------|------|
| **就绪** | `clone_available` = 引擎能 `import f5_tts`（**App 外 F5 Kit overlay** 或应急 clone 内嵌）。**交付主路径** = core 包 + `install-f5-runtime-kit`；**禁止** core 无 Kit 冒充 clone。权重与 `clone_runtime_status`（含 `f5_kit_rev`）见 [`VOICE_CLONE.md`](VOICE_CLONE.md)。 |
| **试听 API** | `POST /voice/packs/preview`：合成后 **单轨 `preview.wav` + 非空 `audio_path`**；macOS **`afplay`** 出声（`playing`）。禁止只读不存在的 `NarrationResult.audio_path` 返回空路径 200。 |
| **设置页** | 独立「保存旁白音色」写 VIDEO_LOCK。 |
| **规则页（防误会 · 2026-08-07）** | **引擎真相源唯一**：`声音与语言 · 音色` → `draft.tts_provider`。「音色管理」**不再另选引擎**（`ruleLink`），只管声色包/试听/导入；改包会写回草稿。底部 **「保存并启用」** 同时写客户 VIDEO_LOCK 并对齐 `voice_pack`。禁止再做两套互不同步的引擎下拉。 |
| **禁止（L19）** | 无授权改就绪核心键/预览汇床/出声合同；禁止常态 merge F5 进 App；禁止忽略 **python_tag** / NumPy≤2.4。 |
| **运维** | Kit：`package-f5-runtime-kit.sh` / `install-f5-runtime-kit.sh` / `verify_f5_overlay_post_install.sh`；LEGACY 急救：`diag_fix_stalled_jobs.py --fix`（下次 core 仍丢）。 |

### 5.3 App ↔ Engine 合同

- 唯一基址：`http://127.0.0.1:<port>`（默认 8766）。  
- 错误展示 API `detail`；网络失败与业务 409（盘未挂载）区分文案。  
- 引擎启停仅 Tauri 侧；浏览器预览模式只连已启动引擎。

---

## 6. 引擎代码标准

### 6.1 目录职责（新增模块何时建）

| 目录 | 何时新建 | 禁止 |
|------|----------|------|
| `engine/pack/` | Phase 2 文案/TTS/字幕 | 里写发布点击逻辑 |
| `engine/reach/` | Phase 4 队列/浏览器配置 | 里写混剪/FFmpeg |
| `engine/platforms/` | 某平台适配器 ≥2 个文件时 | 在 `api/app.py` 堆平台分支 |

### 6.2 API 设计

- 资源命名复数、客户域隐式：`/jobs`、`/assets`、`/outputs`。  
- 显式跨客户管理：`/customers`、`/customers/{id}`、`/customers/activate`。  
- 长任务：立即返回 job/task id，进度走 events 或 status 轮询。  
- 禁止在 API 层出现 `if customer.name == "北京始峰伟业"`。

### 6.3 渲染与质检

- 画布/字幕/音频策略读 **settings + customer.profile + template**，不读客户名。  
- QC 门闩通用；客户可加严（profile），不可在无说明时放松到「无音轨也 ready」。  
- sidecar 必须含：`customer_id`/`customer_name`、`template`、`seed`、`clips`、`qc`、`meta`。

### 6.4 错误与熔断

- 生产熔断与质量熔断分离计数，但都要写入 job snapshot。  
- 对外错误信息可给客户看；堆栈只写日志。

### 6.4b 系统事件与长任务门禁（GSystemPause）

- 凡长任务写入口须调用 `assert_runtime_active()`；暂停期间返回 423。
- 计算任务支持安全检查点与幂等恢复；发布/更新/同步写入中断后转人工确认，禁止自动重放。
- 自动恢复不得解除人工暂停、质量熔断、验证码停人、路径/硬盘异常。详见 [`SYSTEM_EVENT_PAUSE.md`](SYSTEM_EVENT_PAUSE.md)。
- 发布全机执行槽必须跨客户原子互斥；质量补偿只能替代原 occurrence、沿用冻结规则和配额，READY_GATE 通过后方可窗口外立即发布。
- 登录/验证码 human alert 三路独立投递并持久化确认；通知不得包含正文、Cookie、验证码或完整会话。
- 本人多账号即时发布必须先持久化 occurrence 分配快照；自动平均与手动分配总数必须可重放。登录/验证码可停人，处理后自动探测并续跑；结果不明仍禁止盲目重发。
- 定时发布到点现挑 ready 成片，禁止建 trigger 时内容预留；不够立刻补产。即时与定时共享待发池，抢片靠执行时发布组/队列占用。

### 6.5 代码风格

- Python 3.11+；类型注解；SQLAlchemy 2.0。  
- 不引入「为始峰临时」的全局单例。  
- 新依赖写进 `requirements.txt`，并说明用途。

---

## 7. 存储与同步标准

1. `settings.paths.data_root` 指向的库是**唯一权威库**；生产默认固定为本机 APFS `~/Suying/data`，禁止把 SQLite/WAL 放入 ExFAT、SMB 或同步目录。
2. 同步适配器（极空间等）必须插件化：`ops/suying_sync.py` 可换成无同步；产品核心不依赖 NAS 品牌。  
3. legacy 路径映射（旧「手机相册备份」→ 标准客户树）只存在于**该客户同步配置**，不进引擎取片逻辑。  
4. 发布 cookies / 浏览器 profile 仅存工作区 `secrets/` 或系统钥匙串，按 `customer_id` 分目录。
5. 同步下载必须限制在显式目的地沙盒；`data/cache/render/runtime` 为禁写根。不同同步进程仅在目的地无父子/同路径重叠时允许并行。

---

## 8. 测试与验收标准

| 层级 | 要求 |
|------|------|
| 单元/冒烟 | `smoke_test.py` 使用**临时目录 + 演示客户**，禁止依赖始峰盘 |
| 行业包测试 | 用 `building-supply` sample 包 dry-run 可出计划 |
| 多客户隔离 | 激活客户 A 不可看到客户 B 的 assets/jobs |
| 新客户路径 | 自动化或手测清单：向导创建 → 扫空库 → dry-run blocked 合理 → 导入样例 → ready |
| 样板可删 | CI 或脚本：`configs/customers/北京始峰伟业` 不存在时测试仍绿 |
| 质量 | 黄金样片按**当前客户**存放与签字，标尺字段通用 |

---

## 9. 命名与文案标准

| 场景 | 使用 | 避免 |
|------|------|------|
| 产品名 | 速影 / Suying | 对外混用旧名 montage-studio |
| 代码仓目录 | `~/QR/dev/速影` | 指向已废弃的 `montage-studio` 空壳 |
| 包名/模块 | `engine.*` | `shifeng_*` 进主包 |
| 脚本 | `scripts/fixtures/bootstrap_sample_customer.py` | 唯一入口叫 `bootstrap_shifeng` |
| 文档 | 「样板客户：始峰」 | 「系统就是给始峰用的」 |

---

## 10. 功能开发 Definition of Done（通用）

任意功能合并前自检：

- [ ] 不硬编码某一公司名/人名/路径  
- [ ] 读写带 `customer_id` 或经 scope 过滤  
- [ ] 差异化走 profile / 行业包 / 客户目录  
- [ ] App 有对应入口或明确「仅 API」  
- [ ] 冒烟不依赖外置真实客户盘  
- [ ] 文档：PRODUCT_PLAN 相位、本标准、必要时 SOP 已更新  
- [ ] 若涉及发布：人在回路；无「绕检测」表述  

---

## 11. 与路线图的对应（开发优先级）

| Phase | 产品 | 工程标准动作 |
|-------|------|--------------|
| 0 | 可生产 | 权威库唯一；路径健康；去掉错误默认客户依赖 |
| 1 | Studio 质量收口 | Sprint C；hooks/theme **开始**数据化；质量分回填 |
| 2 | Pack | 新建 `engine/pack` + App「物料」Tab；文案适配器按 platform 插件 |
| 3 | 口播同构 | template 支持「时长预算来自 TTS」；仍客户无关 |
| 4 | Reach | 新建 `engine/reach`；浏览器配置按客户隔离；半自动 only |

**重构窗口：** Phase 1 收口同时，把 hooks/theme_rules/template_bindings 迁出代码——这是通用化的最小必要重构，不做会把第二个客户做成 if-else 地狱。

---

## 12. 反模式清单（禁止）

1. `if active_customer == "北京始峰伟业": ...`  
2. 在 `engine/template/hooks.py` 继续追加客户专用句子当默认全局钩子  
3. App 写死片库绝对路径  
4. 把 SQLite 放进极空间同步目录  
5. 为赶工把发布点击逻辑塞进 `render/ffmpeg.py`  
6. 用「绕开抖音检测」作为技术验收  
7. 第二客户靠复制一整份引擎代码  

---

## 13. 一页纸：你在造什么

```text
速影 = 通用本机「剪辑 + 物料 + 辅助触达」工作室
     ├─ 产品内核（代码）：摄入 / 取片 / 渲染 / 质检 / 任务 / 导出 /（pack）/（reach）
     ├─ 行业包（数据）：钩子、主题规则、模板绑定、样例词包
     └─ 客户实例（数据+路径）：片库、成片、词池、品牌、profile、密钥
```

第二个客户到来时：**只新增客户目录 + profile + 词包（可选换行业包）**，不新增产品分叉。

---

## 14. 文档索引

| 文档 | 职责 |
|------|------|
| **本文件** | 最终开发标准（通用化） |
| [`PRODUCT_PLAN.md`](PRODUCT_PLAN.md) | 做什么、分期、卖什么 |
| [`V8_QUALITY.md`](V8_QUALITY.md) | 当前质量冲刺执行 |
| [`V8_STORAGE.md`](V8_STORAGE.md) | 存储布局 |
| [`V8_SUYING_APP.md`](V8_SUYING_APP.md) | App 能力说明 |
| [`V6.md`](archive/V6.md) | 多客户隔离已实现基线 |
| [`configs/samples/`](../configs/samples/) | 通用样例配置 |

**变更本标准：** 需同步改 PRODUCT_PLAN 相关相位，并在 PR/备忘中写明「破坏性：是否要求迁移客户 profile」。

---

## 15. 客户词池与生成文案硬标准

- 每个客户运行时只绑定 `03-词池/keyword-pack.json`；更新稿不得成为长期路径。
- 新版本以 `meta.revision` 与规范化内容 SHA256 判定，mtime 不参与版本选择；旧版原样进入 `archive/`。
- Job 创建时冻结 `keyword_pack.id/revision/sha256/schema`，标题、旁白、字幕、sidecar 和发布物料均使用该快照。
- schema v2 只维护 `facts/taxonomy/copy_components/recipes/compliance`；旧读取结构由编译层派生，禁止双份手工维护。
- 文案先按最终入选切片生成严格语义内容指纹，再选择 recipe 和组件；文件名、行业常识、外观猜测不得补造事实。
- 标题、封面、旁白、字幕、正文、hashtags 统一经过声明门禁；高风险、隐私或缺证据内容在 TTS/READY/publish_pack 前 fail-closed。
