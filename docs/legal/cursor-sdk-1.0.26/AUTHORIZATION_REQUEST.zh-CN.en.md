# Cursor SDK 1.0.26 商业桌面产品再分发授权询问

> 状态：草稿，未发送。收件人建议：`legal@cursor.com`，并抄送销售联系人（如有）。

## 中文

主题：请求确认 `cursor-sdk` 1.0.26 随商业桌面产品离线嵌入和再分发的授权

Anysphere / Cursor 法务团队您好：

我们正在开发一款名为“速影（Suying）”的 macOS 商业桌面产品，计划在少量付费客户的设备上进行私下、受控的离线安装交付。我们希望就 Python package `cursor-sdk` 精确版本 `1.0.26` 的嵌入和再分发取得明确书面确认。

拟议使用方式如下：

- package：PyPI `cursor-sdk`
- version：`1.0.26`
- 产品形态：收费的 macOS 桌面应用，不是通用 SDK 托管、转售或向第三方开放的 Cursor 代理服务
- 嵌入方式：将 package 及其随包运行依赖嵌入 App 的离线 Python runtime；客户安装过程不从 PyPI 或公网下载该 package
- 功能用途：由产品在本机调用 Cursor SDK 的 `Agent.create`、`Agent.resume`、`agent.send` 和流式运行接口，为已授权用户提供辅助式代码/运维代理能力
- 客户规模：初期仅少量付费商业客户
- 账号与计费：不向客户转售我们的 Cursor 账号或共享 API key；我们愿意按 Cursor 要求采用每客户自有账号/API key、团队计划或其他指定商业安排
- 交付方式：App 和依赖通过签名的离线安装仓交付；不会修改 Cursor SDK 的版权和专有权声明

请书面确认：

1. Anysphere 是否授权我们把 `cursor-sdk` 1.0.26 的原始 package 和必要运行依赖嵌入上述商业 macOS 桌面产品，并向少量付费客户离线再分发？
2. 客户是否必须使用其自有 Cursor 账号/API key，或是否允许由我们的商业账号为产品功能提供调用？分别适用什么计划、计费和终端用户条款？
3. 我们需要在 App、安装包或第三方声明中保留哪些 LICENSE、NOTICE、版权、商标或服务条款文本？
4. 此场景是否需要单独的 Order Form、MSA、OEM、再分发或商业许可？如果需要，请提供办理路径。
5. 该授权是否明确覆盖 package 内随附的 Cursor SDK bridge/runtime 文件，以及随 SDK package 正常安装的必要依赖？

在收到能够明确覆盖上述离线嵌入和商业客户再分发场景的书面许可前，我们将保持发布门禁为阻塞状态。

谢谢。  
联系人：[姓名]  
公司/主体：[主体名称]  
邮箱：[邮箱]  
预计客户数：[数量]  
预计月调用量：[数量]

## English

Subject: Request for written authorization to embed and redistribute `cursor-sdk` 1.0.26 in a commercial offline desktop product

Dear Anysphere / Cursor Legal Team,

We are developing a commercial macOS desktop product named “Suying”. We plan to distribute it privately through controlled offline installations to a small number of paying customers. We request explicit written confirmation regarding embedding and redistributing the Python package `cursor-sdk`, exact version `1.0.26`.

Our proposed use is:

- Package: PyPI `cursor-sdk`
- Version: `1.0.26`
- Product: a paid macOS desktop application, not a general-purpose hosted SDK, resale of Cursor, or a service providing third parties standalone access to Cursor agents
- Embedding: the package and its required runtime dependencies are bundled inside the app’s offline Python runtime; customer installation does not download this package from PyPI or the public internet
- Functionality: the local product invokes `Agent.create`, `Agent.resume`, `agent.send`, and streaming run APIs to provide authorized users with assisted coding/operations agent functionality
- Customer scale: initially a small number of paying commercial customers
- Accounts and billing: we will not resell our Cursor account or share an API key with customers; we are willing to require customer-owned Cursor accounts/API keys, a team plan, or another commercial arrangement specified by Cursor
- Delivery: the app and dependencies are delivered in a signed offline installation depot, while preserving all Cursor SDK copyright and proprietary notices

Please confirm in writing:

1. Does Anysphere authorize us to embed the unmodified `cursor-sdk` 1.0.26 package and necessary runtime dependencies in this commercial macOS desktop product and redistribute them offline to a small number of paying customers?
2. Must each customer use its own Cursor account/API key, or may our commercial account provide calls used by the product? Which plan, billing terms, and end-user terms apply in each case?
3. Which LICENSE, NOTICE, copyright, trademark, or Terms of Service texts must be included in the app, installer, or third-party notices?
4. Does this use require a separate Order Form, MSA, OEM, redistribution, or commercial license? If so, please provide the appropriate process.
5. Does the authorization expressly cover the Cursor SDK bridge/runtime files included in the package and the dependencies required by a normal SDK installation?

Until we receive written permission that expressly covers offline embedding and redistribution to commercial customers, our release gate will remain blocked.

Thank you.  
Contact: [Name]  
Company / legal entity: [Entity name]  
Email: [Email]  
Expected customer count: [Number]  
Expected monthly usage: [Amount]
