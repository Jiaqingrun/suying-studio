# 离线仓第三方材料 provenance

取证日期：2026-07-29。所有网络读取均在运维机完成；未上传 T2S，未安装客户机。

## 对应源码

- FFmpeg 8.1.2
  - formula 官方 URL：`https://ffmpeg.org/releases/ffmpeg-8.1.2.tar.xz`
  - 取得位置：VideoLAN 可验证镜像 `https://download.videolan.org/pub/contrib/ffmpeg/ffmpeg-8.1.2.tar.xz`
  - SHA256：`464beb5e7bf0c311e68b45ae2f04e9cc2af88851abb4082231742a74d97b524c`
- x264 r3222
  - 官方仓库：`https://code.videolan.org/videolan/x264.git`
  - commit：`b35605ace3ddf7c1a5d67a2eb553f034aef41d55`
  - 源码归档：官方 GitLab commit archive
    `https://code.videolan.org/videolan/x264/-/archive/b35605ace3ddf7c1a5d67a2eb553f034aef41d55/x264-b35605ace3ddf7c1a5d67a2eb553f034aef41d55.tar.gz`
  - GitLab commit API 返回的完整 `id` 已与 formula revision 精确匹配；归档 SHA256
    记录在 `MATERIAL_SHA256.txt`
- x265 4.2
  - 官方上游：`https://bitbucket.org/multicoreware/x265_git/downloads/x265_4.2.tar.gz`
  - SHA256：`40b1ea0453e0309f0eba934e0ddf533f8f6295966679e8894e8f1c1c8d5e1210`
- LAME 3.101
  - 官方项目下载：`https://downloads.sourceforge.net/project/lame/lame/3.101/lame-3.101.tar.gz`
  - SHA256：`7578af6eebd578b2bd64e468fac4ae1f03670a7e028166e67f855674b9b6aeac`
- mpg123 1.33.6
  - 官方上游：`https://www.mpg123.de/download/mpg123-1.33.6.tar.bz2`
  - SHA256：`929a7c18ba662b8927aed4de229ad9ae8ab2b4806dd0f30b90113eb1b4e2195a`

每个 Homebrew 组件的实际 `.brew/*.rb` 与 `INSTALL_RECEIPT.json` 位于
`homebrew-evidence/`。x264 实际 formula 固定 `version "r3222"` 与上述完整 commit；
FFmpeg formula 固定 8.1.2、`revision 1`，并启用 `--enable-gpl` 和
`--enable-version3`。本批 formula 没有外部 patch stanza；源码 URL、固定 SHA 与
构建参数均由配方本身保留。

## 已下线组件的历史取证

以下材料仅保留供历史审计，不进入当前客户 App 或 legal 交付组件。

### Remotion 4.0.484

- 官方 tag：`https://github.com/remotion-dev/remotion/releases/tag/v4.0.484`
- tag ref：`v4.0.484` → commit
  `97b7207325ffb7f338b2139301f46ad52de53eab`
- 同 tag `packages/core/package.json`：name=`remotion`，version=`4.0.484`
- 同 tag LICENSE：`https://raw.githubusercontent.com/remotion-dev/remotion/v4.0.484/LICENSE.md`

由于商业桌面 runtime 嵌入/再分发范围不明确，产品已移除 Remotion 与
OpenMontage creative runtime，不再以取证材料尝试解除分发门禁。

### Cursor SDK 1.0.26

- App 内 package LICENSE 只声明 © Anysphere、保留全部权利，并指向 Cursor Terms。
- 当前公开 Terms 第 1.1 条只授予有限访问/使用权；第 1.5 条限制复制、修改、出租、
  出借或销售 Service；第 5.1 条声明没有默示许可。
- Python SDK 文档证明 package 的公开调用用途，但没有明确授予离线嵌入收费桌面产品
  并向商业客户再分发 package 的权利。

公开文本不足以替代书面授权，因此产品已删除 Cursor SDK 生产依赖、API、助手和
发布故障接管入口。中英询问模板未发送，仅作为历史记录保留。
