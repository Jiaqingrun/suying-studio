# 速影桌面端

Tauri 2 + React 桌面壳，产品名 **速影**（`com.qr.suying`）。

## 开发

```bash
cd apps/desktop
npm install
npm run tauri dev
```

## 打包（macOS 交付）

```bash
cd apps/desktop
npm run package:mac
```

产出：

- 桌面 `速影.app`（日常打开）
- 桌面 `速影-<版本>-macos-<架构>/` 交付夹（App + DMG + 安装说明 + SHA256）
- 桌面 `速影-<版本>-macos-<架构>.zip`（方便私发）

引擎仍由本机 `montage-studio` 提供（见安装说明 / `SUYING_ROOT`）。
