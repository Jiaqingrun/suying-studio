# 内嵌 Python 运行时许可证汇总

> Manifest 字段：`legal/python-runtime/THIRD_PARTY_LICENSES.txt`  
> 生成器：`scripts/build_legal_component.py`（从已构建的 `速影 Studio.app` 内嵌 CPython 收集 `LICENSE.txt` 与 `.dist-info` 许可证）

## 为何源码仓默认没有正文文件

该汇总随 **App 内嵌 site-packages 闭包** 变化，不宜手写冻结在 Git。  
离线 legal 组件组装时由构建脚本写入；`distribution_status=ready` 时必须存在于 legal 组件内。

## 运维如何生成（有 App 时）

```bash
# 默认读 release bundle 路径，或传入 --app
python3 scripts/build_legal_component.py --output /tmp/suying-legal-out
# 产物内应含 legal/python-runtime/THIRD_PARTY_LICENSES.txt
```

## 验收

- `THIRD_PARTY_MANIFEST.json` 中该 `license_paths` 指向的文件在 legal 组件内真实存在  
- 内容含 CPython `LICENSE.txt` 与至少若干 `.dist-info` 许可节  
- 不得含 `cursor_sdk`（构建脚本 fail-closed）
