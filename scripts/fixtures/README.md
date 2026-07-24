# 样板客户 Fixtures（非产品入口）

这里的脚本只服务 **开发机上的样板客户数据**（如北京始峰伟业），不是通用安装路径。

| 脚本 | 用途 |
|------|------|
| `bootstrap_shifeng.py` | 向本机 settings 写入样板路径（需已挂载盘） |
| `convert_shifeng_keyword_pack.py` | 从 markdown 生成样板词包 |

新产品客户请用：**速影 App 向导** 或 `POST /customers`。

权威流程：[`docs/DEV_LOCK.md`](../../docs/DEV_LOCK.md)。
