# 第二客户 · 零分叉验收（P4）

> **状态：✅ 通过（2026-07-25）**  
> **目标：** 新增第二家客户只靠 App/API 配置与目录，**不改引擎业务硬编码**。  
> **依据：** [`PRODUCT_PLAN.md`](PRODUCT_PLAN.md)「第二个客户零分叉」· [`DEVELOPMENT_STANDARDS.md`](DEVELOPMENT_STANDARDS.md)

## 验收清单

| # | 项 | 通过标准 | 状态 |
|---|----|----------|------|
| Z1 | 创建客户 | `POST /customers` 仅 name + library_root + output_root 成功 | ✅ `smoke_zero_fork.py` |
| Z2 | 激活切换 | activate 后 `/health.active_customer` 切换 | ✅ |
| Z3 | 列表隔离 | 激活 B 时看不到 A 的 jobs/assets | ✅ |
| Z4 | 写隔离 | B 下新建 job 的 `customer_id`=B；切回 A 不可见 | ✅ |
| Z5 | 目录约定 | `ensure-customer` 建片库·成片·`05-品牌/封面模板` | ✅ 实盘「零分叉演示客户」 |
| Z6 | 行业包 | 同引擎挂 `_blank` / `building-supply`，无客户名分支 | ✅ |
| Z7 | 代码扫描 | `engine/` 无真实客户名硬编码 | ✅ |
| Z8 | 文档 | 本清单 + 冒烟可重复 | ✅ |

## 冒烟命令

```bash
python3 scripts/smoke_zero_fork.py
```

（临时客户名 `零分叉验收-*`，用系统临时目录；跑完可删库行或保留作废。）

## 明确不算分叉

- 新增行业包 JSON / 客户 profile  
- App 文案与路径填写  
- 本机 settings 的 `active_customer`  

## 算分叉（禁止）

- 在 `engine/` 写死客户名、品类、文案  
- 为第二客户复制一整份引擎/App  
- 共享未按 `customer_id` 过滤的表查询  
