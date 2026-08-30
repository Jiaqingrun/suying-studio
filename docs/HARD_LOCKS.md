# 速影 · 硬规则总锁（HARD LOCKS）

> **效力：写死。2026-07-26 用户明确：「以上规则全部写死」。**  
> **本文件 = 索引。细则以各分锁为准；改阈值 / 绕开门禁 = 必须用户当面批准。**  
> Agent 改相关代码前必读本文件 + 对应分锁。

---

## 分锁清单（全部强制）

| ID | 文件 | 写死内容 |
|----|------|----------|
| L0 | [`DEV_LOCK.md`](DEV_LOCK.md) | 产品边界、Gate、DoD |
| L1 | [`VIDEO_LOCK.json`](../configs/customers/北京始峰伟业/brand/VIDEO_LOCK.json) + `engine/pack/video_lock.py` | 片型/标题/字幕/旁白/语速 + **quality**；样式按横/竖画幅规则冻结并服从 L13 安全几何 |
| L2 | [`NARRATION_SUBTITLE_LOCK.md`](NARRATION_SUBTITLE_LOCK.md) | H1–H9：话说完字幕灭；边距；短句呼吸；rate=-8%；**字幕只烧一次（禁二次叠烧）** |
| L3 | [`EMOJI_STICKER_LOCK.md`](EMOJI_STICKER_LOCK.md) | 旁白不读表情；**字幕行内表情**；禁标题区贴纸 |
| L4 | [`QUALITY_LOCK.md`](QUALITY_LOCK.md) | 虚焦模糊不入库、不向量化、不选片 |
| **L5** | **[`READY_GATE.md`](READY_GATE.md)** | **进成品库总门禁：逐项全过才 ready，否则打回** |
| **L6** | **[`PAPER_SLIP_LOCK.md`](PAPER_SLIP_LOCK.md)** | **纸片滚动避重：近窗优先排除 + 渐进放宽；禁止日/周满额熔断** |
| **L7** | [`DEV_LOCK.md`](DEV_LOCK.md) + `engine/reach/message_sync.py` | **消息静默巡检固定 1800 秒；账号独立；每账号仅留最近30天/最多200条写入前脱敏摘要；本地已读不复活；通知脱敏、去重、可审计** |
| **L8** | [`DEV_LOCK.md`](DEV_LOCK.md) GVideoRules + `engine/template/rule_schema.py` | **规则按客户+内容类别+横/竖画幅隔离；手动/AI 草稿均须保存启用；Job 冻结 revision+完整样式/声音；禁止放松 READY_GATE/画质/声画对齐/纸片** |
| **L9** | [`SYSTEM_EVENT_PAUSE.md`](SYSTEM_EVENT_PAUSE.md) | **系统睡眠/熄屏暂停；唤醒/解锁恢复；自动恢复不得解除人工暂停/熔断/验证码停人；副作用任务禁止自动重放** |
| **L10** | [`SEMANTIC_PIPELINE.md`](SEMANTIC_PIPELINE.md) | **全库 VLM 回填默认停用；粗索引只供普通生产；strict v1 仅由候选片段 9B 按需验证获得，不得把 coarse 冒充 strict** |
| **L11** | [`INSTALL_PROFILE.md`](INSTALL_PROFILE.md) | **首启必须按实测配置生成安装计划；App/远程部署共用档位、模型与门禁；完成须有脱敏回执；禁止按机型名猜配置** |
| **L12** | [`REMOTE_DEPLOY.md`](REMOTE_DEPLOY.md) | **远程部署唯一正式路径：离线单 ZIP + 签名工具/模型 + 单机许可；GUI Keychain 与 LaunchServices；本机 APFS 权威库/profile；覆盖只换 App 并保留登录态；热补不算交付** |
| **L13** | [`SEMANTIC_OBJECT_VECTOR_LOCK.md`](SEMANTIC_OBJECT_VECTOR_LOCK.md) | **coarse/strict 与目录/画面证据分层；标题/字幕按画幅安全几何与冻结规则验收；物品名不侵入安全区；横/竖向量分别最老优先、只入队、真实状态与暂停保留已完成** |
| **L14** | —（**已废止 · 2026-08-13 用户当面要求**） | ~~视频四平台作品描述末行强制「本作品由AI生成」~~ → **成片物料禁止再出现该披露及「本视频由AI生成」等别名；出包/预填/门禁一律剔除** |
| **L15** | [`READY_GATE.md`](READY_GATE.md) + `engine/qc/ready_gate.py` + `engine/render/ffmpeg.py` + `engine/pack/tts.py` | **有旁白 wav 时成片 duration 必须严格大于完整旁白（≥+0.05s）；禁止裁到等长/更短。优先对过长口播做 tempo 贴齐画面（`fit_narration_to_picture_duration`，默认开；加速上限见 `narration_fit_max_speed`）；仍短则冻结尾帧；规则层禁止放松键** |
| **L16** | [`ORIENTATION_LOCK.md`](ORIENTATION_LOCK.md) + `engine/ingest/orientation.py` | **横竖屏显示尺寸（含旋转元数据）双源一致硬审核；失败 `rejected_orientation`；禁止二次 transpose；向量/入库 fail-closed；App 硬性展示** |
| **L17** | [`DEVELOPMENT_STANDARDS.md`](DEVELOPMENT_STANDARDS.md) §5.2.1 + `apps/desktop/src/shell/LangCombobox.tsx` | **规则页旁白/字幕语言下拉：Portal→body + fixed；已验收冻结；禁止回退卡片内 absolute、禁止靠拆 overflow 修裁切；默认禁止再改本组件与 `.lang-combo*`** |
| **L18** | [`V8_STORAGE.md`](V8_STORAGE.md) · [`STORAGE_SYNC_SAFETY.md`](STORAGE_SYNC_SAFETY.md) + `engine/config/settings.py` / `workspace.py` | **Local-first 主盘工作区：权威库 `~/Suying/data`；媒体默认 `~/Movies/速影工作区`；`external_required` 默认 false；自动同步目标为主盘；外置盘仅用户改路径后可选；外卷权威库 fail-closed；**已验收冻结，禁止再改拓扑/默认路径/插盘中心语义** |
| **L19** | [`VOICE_CLONE.md`](VOICE_CLONE.md) + `engine/pack/voice_clone.py` / `engine/pack/f5_kit.py` / `engine/api/app.py` `/voice/packs/preview` + 设置页音色 UI | **本地 F5 克隆旁白就绪与试听（L19）**：就绪键与试听合同冻结；**交付主路径 = core App + App 外 F5 Runtime Kit**（`~/Suying/runtime`），clone flavor 仅应急；禁止无 Kit 宣称 clone 就绪 / 常态 merge 进 App |
| **L20** | [`SERVICE_NARRATION_LOCK.md`](SERVICE_NARRATION_LOCK.md) + `engine/pack/ollama_narration.py` / `narration_script.py` / `copy_diversity_gate.py` | **生活服务旁白（L20 v2）**：2026-08-29 授权受控多样改写 + 近窗去重；保留倾听禁词；无新授权禁止回退黄金硬锁 |

---

## 不可协商底线（摘要）

1. **画质**：Laplacian≥48、score≥0.35；`rejected_blur` 无 embedding、不进成片候选。  
2. **字幕**：对齐 VO；左右 margin≥48；无背景条；**成片已烧则出包禁止再烧（H9）**。
3. **旁白**：先断句再去标点；呼吸≤18 字；Edge `rate=-8%`、`pitch=+35Hz`、`volume=+12%`（情感）。  
4. **表情**：只进字幕行内；禁标题区贴纸；旁白绝不朗读。**烧录：凡含 emoji 的字幕 cue 必须 Twemoji 贴图（继承生产规则色/描边，含 stroke_width=4）；禁止 `stroke_width==3` 等风格门栏挡掉 Twemoji；合成失败 fail-closed。升级不得回退该路径。**  
5. **Ollama**：模块必须可 import；失败写 `ollama_narration_error`，禁止静默跳过。  
6. **标题**：竖屏推荐黄字黑描边/顶部 220px，横屏推荐顶部 120px；规则实验室可在画幅安全范围内改颜色、描边、字号、对齐、淡入和绝对顶部距离。渲染结果必须与 Job 冻结值一致且不越界；旧模板 offset 不得冒充最终字形验收。
7. **成品库**：未过 [`READY_GATE.md`](READY_GATE.md) **不得**标 ready。  
8. **纸片**：滚动避重（cliplet 近 20→10→0；短句 15→8→0）；仅 ready 计入近窗；同任务去重；**禁止**日/周满额硬拒与配额熔断。
9. **消息巡检**：固定每 1800 秒；正式 Chrome `--headless=new` 无可见窗口；消息 Chrome profile 与发布 profile 独立；每账号仅保留最近 30 天、最多 200 条写入前脱敏摘要与身份墓碑；已读只改速影本地且重扫不复活；登录/验证码停人；App、macOS、可选 ntfy 仅发脱敏摘要和官方链接。
10. **系统暂停恢复**：自动恢复只清系统事件持有的暂停；人工暂停 / 质量熔断 / 验证码停人 / 路径与硬盘异常 / 发布结果不明不得解除；发布·更新·同步写入禁止自动重放。
11. **语义分层**：新素材可用 `coarse.v1` 快速入索引；严格选片只认通过原门禁的 `semantic.v1`；禁止自动全库 VLM 与自动 27B。
12. **批量安装**：首启先 Probe/Plan；计划批准后才能配置和安装；无匹配 `install-receipt.json` 不得报告批量部署完成。
13. **远程部署**：只执行 [`REMOTE_DEPLOY.md`](REMOTE_DEPLOY.md) §0；默认禁止 brew / `ollama pull`；许可证只认 GUI 会话 Keychain 的真实设备键；Chrome 必须经 LaunchServices 启动；`montage.db` 与 Chrome profile 必须位于本机 APFS；覆盖不得删除登录态，scp 热补不得冒充正式版本。
14. **证据分层**：`coarse.v1` 不得冒充 `semantic.v1`；官方目录只证明目录事实，不得冒充画面直接可见事实或替代 strict 视觉门禁。
15. **表达几何**：字幕竖屏推荐底部 420px、横屏推荐 180px；颜色、描边、字号、对齐和绝对底部距离可按画幅安全范围配置，仍须声画对齐、静音空屏且只烧一次。物品名称中文单列自上而下，可左/右且不得侵入标题/字幕安全区。
16. **向量运营**：按资格时间最老优先；开始操作只入持久队列；状态必须来自持久化事实；暂停放弃当前未完成请求但保留已完成向量。
17. **AI 披露（L14 已废止）**：成片物料**禁止**再出现「本作品由AI生成」「本视频由AI生成」等披露行；出包/预填/门禁/CDP 一律剔除，不得再强制追加。
18. **成片×旁白时长**：有旁白时成片时长必须严格大于旁白（至少 +0.05s）；混音收尾禁止 trim 到等长/更短，应补尾帧/freeze；READY_GATE `duration: video_must_exceed_narration` 打回。
19. **横竖屏**：以含旋转的显示尺寸为准；源片与归一化成片双源一致才可 ready/向量化；不一致与近平方 fail-closed（`rejected_orientation`）；App 展示硬审核、不可软关。
20. **语言下拉（L17）**：`LangCombobox` 必须 Portal 到 `document.body` + `position: fixed` 视口定位；**不得**再改为卡片内 absolute/relative 弹层；**不得**用去掉 `.rule-card` / `.page-section` 的 overflow 当修法。2026-08-07 人验收通过后**冻结**，除非回归且用户明确授权，否则禁止再改 `LangCombobox` 与相关 `.lang-combo*` CSS。一体包前端嵌入二进制，改源码须 rebuild/换包后验收，只开旧 `/Applications` 无效。
21. **主盘工作区（L18 · Local-first）**：权威库固定本机 APFS `~/Suying/data`；媒体默认 `~/Movies/速影工作区`；载体 `~/Suying/carrier`；`external_required` 默认 **false**，App **禁止** 存路径时再写 true。自动同步 / 刷新默认服务主盘工作区，**不得**再把「必须插外置盘 / 插盘才自动同步」当产品默认。外置路径仅为用户或运维显式覆盖；外卷上的 `data_root` 仍 fail-closed（禁 mkdir 空库）。2026-08-07 用户要求写死后**冻结**：无用户当面授权，**禁止**再改默认拓扑、`reconcile_local_first_paths` 自愈语义、工作区 probe local-first、或把 QR-Volume/外置布局重新写成现行默认。
22. **本地克隆 TTS / 试听（L19）**：**交付主路径** = **core 一体包** + **App 外 F5 Runtime Kit**（`~/Suying/runtime/f5_site_packages` + `f5_kit.manifest.json`；`apply_f5_site_overlay`）。`RUNTIME_FLAVOR=clone` 内嵌 f5 仅应急一体机，**不是** T2S 周更默认。`provider=clone` 机必须 `clone_available`（import f5-tts via overlay 或应急 bundle）+ 权重策略；**禁止** core 包无 Kit 冒充 clone 就绪；**禁止**常态 `diag_fix` merge 进 App（LEGACY，下次 core 必丢）。设置页「试听一句」经 `POST /voice/packs/preview`：合成后必须汇成单轨 `preview.wav`；macOS `afplay`。Kit 与 App **python_tag** 须一致；NumPy≤2.4 pin 于 materialize。运维：`package-f5-runtime-kit` / `install-f5-runtime-kit` / `verify_f5_overlay_post_install`。2026-08-07 试听验收冻结语义；同日固化 Kit 交付拓扑。无当面授权禁止再改就绪核心键与预览合同行为。
23. **生活服务旁白（L20）**：生活服务/店介绍口播 **黄金底稿优先**；Ollama 开启时只允许节奏润色（断句/气息），**禁止**自由串改金句、换「咱们/聊聊天」、删预约安全感句、写「请听」代替「倾听」。漂移必须回退底稿。禁「安排到店服务 / 来之前需要怎样安排 / 会提前和您讲清楚」等空垫。短句呼吸与 `rate=-8%` 仍服从 L2。2026-08-09 用户听感验收通过后**冻结**；细则 [`SERVICE_NARRATION_LOCK.md`](SERVICE_NARRATION_LOCK.md)。

---

## 代码锚点

| 规则 | 锚点 |
|------|------|
| 画质常量 + `assert_quality_lock_integrity` | `engine/ingest/quality.py` |
| 入库拒糊 | `engine/ingest/metadata.py` |
| 切片拒糊 | `engine/ingest/cliplet.py` |
| 向量化拒糊 | `engine/catalog/vector_index.py` |
| 选片拒糊 | `engine/template/engine.py`（floor≥`MIN_QUALITY_SCORE`） |
| 加载时钳制锁（含横/竖样式安全范围） | `engine/pack/video_lock.load_video_lock` + `engine/template/rule_schema.validate_and_clamp` |
| **进 ready 总门禁** | `engine/qc/ready_gate.evaluate_ready_gate` ← worker 拒绝 ready |
| **成片 > 旁白时长** | `ready_gate._check_duration` + `ffmpeg.render_plan` 冻结尾帧；`rule_schema.FORBIDDEN_KEYS` 禁放松 |
| **横竖屏硬审核 L16** | `engine/ingest/orientation.py` + `metadata.ingest_file` + `vectorization_runtime.enqueue_asset`；App `VectorControl` |
| **纸片滚动避重** | `engine/catalog/paper_slip` + `engine/template/engine` 近窗 exclude / 渐进放宽；ready 记账；不得配额熔断 |
| **消息巡检与通知** | `engine/reach/message_sync.py` + `chrome_runtime.py` + `notifications.py`；SQLite CHECK/trigger 锁定 1800 秒 |
| **系统事件暂停** | `engine/runtime/pause_coordinator.py` + Tauri `power_events`；`/system/*`；423 长任务门禁 |
| **粗索引 / 按需 9B** | `engine/ingest/semantic_gate.py` + `cliplet.py`；`/index/captions/verify` |
| **机型安装计划 / 回执** | `engine/ops/install_profile.py`；`/setup/install-plan*`；`scripts/remote-install.sh` |
| **统一远程部署** | `scripts/deploy-remote.sh` + `scripts/remote-install.sh`；`engine/reach/chrome_runtime.py` |
| **GSemanticOps 专项锁** | `docs/SEMANTIC_OBJECT_VECTOR_LOCK.md`；实现锚点在 GSO.2–GSO.5 完成前不得虚构为已落地 |
| **语言下拉 L17** | `apps/desktop/src/shell/LangCombobox.tsx` + `App.css` `.lang-combo*`；细则 [`DEVELOPMENT_STANDARDS.md`](DEVELOPMENT_STANDARDS.md) §5.2.1 |
| **主盘工作区 L18** | `engine/config/settings.py`（默认路径 / `reconcile_local_first_paths`）；`engine/config/workspace.py`；`engine/ops/suying_sync.py`；App `savePaths` / `WorkspaceSyncControl`；详情 [`V8_STORAGE.md`](V8_STORAGE.md) |
| **本地克隆 / 试听 L19** | `engine/pack/voice_clone.py` + `engine/pack/f5_kit.py`；`POST /voice/packs/preview`；`scripts/package-f5-runtime-kit.sh` / `install-f5-runtime-kit.sh` / `verify_f5_overlay_post_install.sh`；细则 [`VOICE_CLONE.md`](VOICE_CLONE.md) |
| **生活服务旁白 L20** | `engine/pack/ollama_narration.py`（`use_golden_base_lock` / retention / drift）；`engine/pack/narration_script.py`（warm + 服务禁词）；`engine/content/content_fingerprint.py`（hook 别名 + 加权）；细则 [`SERVICE_NARRATION_LOCK.md`](SERVICE_NARRATION_LOCK.md) |

---

## 冒烟

```bash
python3 scripts/smoke_hard_locks.py
python3 scripts/smoke_ready_gate.py
python3 scripts/smoke_paper_slip.py
python3 scripts/smoke_subtitle_align.py
python3 scripts/smoke_subtitle_margin.py
python3 scripts/smoke_narration_breath.py
python3 scripts/smoke_emoji_stickers.py
python3 scripts/smoke_system_events.py
```

---

## 变更

| 日期 | 事件 |
|------|------|
| 2026-07-26 | 用户：虚焦规则写死；「以上规则全部写死」→ 建本总锁 + VIDEO_LOCK.quality + 加载钳制 |
| 2026-07-26 | 用户：标题改黄字黑描边；全套审核门禁 → `READY_GATE.md` + worker 强制 |
| 2026-07-26 | 用户：标题整体下移 120px → `offset_y_px` 写死 |
| 2026-07-26 | 用户：纸片规则日≤2 → `PAPER_SLIP_LOCK.md` + `daily_usage` |
| 2026-07-26 | 全量自检：READY_GATE 缺 clips / 缺 tts_provider 改为 fail-closed；Twemoji 去硬编码盘路径；DEFAULT_LOCK 中性化 |
| 2026-07-26 | 收口复检：纸片上限下沉 SQLite CHECK/trigger；产品核心去客户及建材行业默认；打包后强制内嵌 health + codesign 验签 |
| 2026-07-26 | 用户明确授权消息巡检完整工作流：固定 1800 秒、正式 Chrome 静默 headless、三路通知与 ntfy 安全配置 |
| 2026-07-27 | 用户批准低配 Mac 分层语义：停止全库回填；coarse 普通生产 + 候选片段 9B 单次验证；27B 不自动调用 |
| 2026-07-27 | 用户要求批量安装硬化：首启检测、Install Profile v1、自动模型配置、人工边界与脱敏回执 |
| 2026-07-29 | 用户要求将 `xlf-remote` 真机成功经验固化为统一远程部署标准 → L12 |
| 2026-07-30 | 用户授权 GSemanticOps 文档阶段 → L13；替换旧标题 offset 验收口径，新增证据分层、日周双限额、物品标注与向量运营硬锁 |
| 2026-07-30 | GSO.3：上海自然日/自然周双限额落地；并行 Job SQLite 原子预留、ready 幂等提交、失败释放与过期回收 |
| 2026-07-30 | 用户要求视频平台作品描述强制末行「本作品由AI生成」→ L14 |
| 2026-08-13 | 用户当面要求成片物料完全去除 AI 披露字样 → **L14 废止**，改为强制剔除 |
| 2026-07-31 | 用户废止纸片日/周满额硬拒 → L6 改为滚动避重；禁止配额熔断 |
| 2026-07-31 | GCustomerUX P0：成片时长 > 旁白时长硬锁 → L15；全局 serial；标题 max_chars=24 |
| 2026-08-01 | 用户明确批准：固定黄/黑/220px 与白/黑/420px 改为横/竖画幅分别可配置；安全范围、Job 冻结、READY_GATE 一致性与其余质量锁继续强制；新增 1080×1920 / 1920×1080 双画幅合同 |
| 2026-08-06 | 用户要求横竖屏严谨审核并 App 硬性化 → L16 ORIENTATION_LOCK（双源一致 / fail-closed / 禁二次 transpose） |
| 2026-08-07 | 用户：主盘工作区 Local-first 已落地并明确「以后不再做任何变更」→ **L18 冻结** |
| 2026-08-07 | 用户：本地 F5 就绪可选 + 试听出声已验收，要求勿再改相关代码 → **L19 冻结** |
| 2026-08-09 | 用户：生活服务旁白听感已可接受，要求写死、在主动要求前禁止再改 → **L20 冻结** |
