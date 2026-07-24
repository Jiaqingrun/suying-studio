# 操作 SOP（MVP）

## 1. 每日开机
1. 确认外置资料盘已挂载
2. 启动 Montage Studio 引擎与桌面 App
3. 打开「系统」页确认状态为 ok/degraded 且无 error

## 2. 投递素材
1. 按分类文件夹放入资料库（如 `library/门店/`）
2. App → 素材 → 「扫描资料库」
3. 确认列表中出现新素材且状态为 ready

## 3. 更新词包
1. App → 系统 → 选择客户名
2. 上传 `.json` 或含 JSON 的 `.md` 词包文件

## 4. 出片前 Dry-run
1. App → 任务 → 填写主题/分类
2. 点击「Dry-run 预览」
3. 确认标题、片段列表无 blocked 与严重 warnings

## 5. 创建无人值守任务
1. 设置目标条数或（后续）运行时长
2. 点击「创建任务」
3. App → 任务 查看 produced_count 与状态

## 6. 成片位置
- 成功：`output/ready/YYYY-MM-DD/montage_*.mp4`
- 失败：`output/failed/YYYY-MM-DD/`
- 每条成片旁有 `.json` 溯源与文案侧车

## 7. 日志与导出
- App → 日志 → 导出 CSV
- 路径默认在 `~/MontageStudio/data/exports/`

## 8. 故障处理
- 连续失败任务进入 circuit_open → 暂停后检查素材/词包/磁盘，再恢复
- 引擎离线 → 重启 `./scripts/start-engine.sh`
