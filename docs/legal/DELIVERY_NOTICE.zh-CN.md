# 速影离线仓第三方软件交付说明

本交付构建不包含 OpenMontage、Remotion 或 Cursor SDK。速影核心成片由 FFmpeg
渲染；App 内嵌 Studio Python 引擎与可迁移 CPython。

legal 组件必须包含：

- `THIRD_PARTY_MANIFEST.json`（`distribution_status=ready`、`gaps=[]`）；
- FFmpeg 闭包全部 LICENSE/COPYING；
- FFmpeg、x264、x265、LAME、mpg123 对应源码归档、实际 formula 与 receipt；
- Ollama 和模型许可证及精确 provenance；
- 本说明。

Remotion/OpenMontage/Cursor SDK 的历史取证材料不属于当前交付闭包，不得装入
legal 组件或客户 App。签名、CAS、源码、runtime manifest 或覆盖关系任一不闭合，
构建、bootstrap、物化和上传均须 fail closed。

本说明不是法律意见。
