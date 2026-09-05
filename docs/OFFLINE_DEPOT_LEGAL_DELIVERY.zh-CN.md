# 速影离线仓第三方软件交付说明


> 冲突时：`DEV_LOCK.md` / `HARD_LOCKS.md` > 本文。权威索引见 [`README.md`](README.md)。
## 当前结论

审计对象：`/Users/qr/Desktop/速影-offline-depot-arm64-ready`。客户 App 已移除
OpenMontage/Remotion creative runtime 与 Cursor SDK 生产依赖、路由和界面入口。
重新构建的 App 不含 `runtime/creative`，内嵌 Python 不含 `cursor_sdk`。

`docs/THIRD_PARTY_MANIFEST.json` 当前为 `distribution_status=ready`、`gaps=[]`。
上述材料已组装为 legal 组件并重建、重签、全量验证 depot seq=3；Python/Rust
bootstrap 的 lite/pro 物化均通过，状态为 `READY_UPLOAD`。本轮仍禁止上传 T2S。

## 保留的分发组件

- 速影 App：Tauri 壳、Studio Python 引擎和可迁移 CPython；核心视频渲染使用
  FFmpeg，不依赖 Remotion。
- FFmpeg 8.1.2_1 及其 Homebrew 动态库闭包。
- Ollama 0.24.0。
- `nomic-embed-text` 与 `qwen3.5:9b` 精确 Ollama manifest 对应模型。

## 法律材料闭包

- FFmpeg、x264、x265、LAME、mpg123 对应源码归档位于
  `docs/legal/source-offer/`，固定摘要见 `docs/legal/MATERIAL_SHA256.txt`。
- Homebrew formula 与安装 receipt 位于 `docs/legal/homebrew-evidence/`。
- FFmpeg 闭包各许可证位于 `docs/legal/licenses/`。
- 两个模型的许可证正文与精确 manifest provenance 位于
  `docs/legal/model-licenses/` 和 `docs/legal/PROVENANCE.md`。
- Remotion、OpenMontage、Cursor SDK 的旧取证材料只保留为历史审计记录，不得进入
  新 legal 组件，也不得据此恢复已下线能力。

## fail-closed 条件

外部分发仍须满足：

1. depot 只有一个 `legal:legal:any` 组件；
2. legal 清单覆盖全部非 legal 组件，状态为 `ready` 且 `gaps=[]`；
3. 每个许可证与 `source_required` 对应源码均为 legal 组件内真实文件；
4. depot 签名、CAS 闭包、架构、空间预算、物化后 SHA256 全部通过；
5. App runtime manifest 验签并覆盖全部 runtime 文件；
6. App 内不得出现 `runtime/creative`、`cursor_sdk` 或 Cursor API/助手入口。

本说明不是法律意见；它记录可复核的工程与材料事实。
