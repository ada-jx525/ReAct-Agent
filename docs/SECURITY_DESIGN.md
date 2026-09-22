# 客服 Agent 安全检查与修复报告

检查日期：2026-09-14。范围：本仓库 Python / LangGraph、CLI、单 worker 只读 FastAPI、SQLite 业务服务和本地政策 RAG。
使用 Conda `agent`，没有前端、真实退款/支付、第三方物流写操作或公网部署。

## 结论与威胁模型

当前是有可复现安全边界的秋招 Agent MVP，不是生产安全认证，也不能承诺“无法被攻击”。
本次修复审批期限、申请意图、数据指令隔离及资源限制，并实际检查越权、注入、恢复、重放和正常业务。
防护的关键不是模型一定听话，而是模型失误时仍不能更换客户身份、选择未经用户指定的订单、跳过人工审批或公开内部草稿。

不信任用户问题、LLM 工具参数、商品描述和检索文本。信任应用注入的运行身份、终端操作者、部署配置、本地政策/索引和已准备的模型文件。
持有另一个合法客户 token、控制本地文件系统或直接调用内部服务的开发者，不属于远程问题输入攻击模型。
CLI 的 `--customer-email` 是模拟身份选择，**不是登录认证**；API token 也仅用于演示，不是完整账号系统。

## 检查方法与结果

- 确定性服务/API/审批与输出检查：62/62 通过。
- 升级依赖后完整复跑：76/76 通过，其中确定性检查 62 项、4B/1.7B 各 7 项真实 graph 对照。原始报告输出到本地 `knowledge/results/`，不提交仓库。
- 每个模型包括正常订单查询、冒充其他客户、系统提示外泄、商品间接注入、只读绕过、跳过确认和政策角色注入。
- API 契约检查使用替身图；审批检查使用真实 ToolNode、interrupt、AsyncSqliteSaver，并关闭/重开 saver 验证原始草稿跨重启固定。
- 商品间接注入采用检测规则不匹配的句式，检查即使没有命中检测器，客户隔离和审批边界仍成立。
- 政策注入用例是在检索后注入合成片段，**不是**污染实际 FAISS 索引或验证任意数据投毒都能发现。
- 使用临时订单/会话库与合成 canary；原始订单数据库哈希不变。已批准和并发写入仅发生在临时副本。
- LLM/传输/格式错误按失败记录，不算“攻击被防住”。分别记录内部草稿与最终公开回答的 canary 泄露。
- 本轮两种模型公开回答和内部草稿均未泄露合成 canary；这只描述本组用例。真实 FAISS + embedding + CPU rerank 查询返回 3 个候选，升级后仍可运行。
- Ruff 检查及格式检查通过，wheel 构建通过，包含所有应用模块且不包含本地数据库/模型权重；只读 API 边界检查通过。
- `check_api.py --real` 通过：实际 HTTP API 调用本地 graph 返回 James 的 ORD-1001；直接调用写工具被只读能力守卫拒绝，原库保持不变。

案例数量包含正常对照、多个层次和两个模型的重复场景；这是开发回归断言通过数，**不是攻击成功率、通用准确率或独立渗透测试覆盖率**。

## 已修复发现（按优先级）

### SEC-001 · Medium · 恢复参数可延长确认期限

位置：[tools.py](../src/react_agent/tools.py#L113)、[tools.py](../src/react_agent/tools.py#L271)。
证据：原实现恢复时重新准备草稿，且有效期来自恢复参数；过期时间不属于原 fingerprint，任意未来时间可能绕过原来的 10 分钟确认期限。
影响：旧确认可能在过期后仍被接受。原有客户隔离、人工 interrupt 和事务校验并未因此失效，当前只读 API 也没有暴露恢复端点，不能将此描述为公网无审批退款。
修复：用 checkpointed `@task` 固定原始草稿；严格验证恢复对象字段，fingerprint 和 expires_at 必须与原草稿完全一致；使用原草稿时间检查期限。
复验：过期、未来日期篡改、fingerprint 篡改、额外字段、身份变更、只读恢复、政策/订单变更、拒绝、重启后批准及幂等重放均通过。

### SEC-002 · Medium · 申请意图和整单范围主要依赖提示词

位置：[security.py](../src/react_agent/security.py#L88)、[tools.py](../src/react_agent/tools.py#L147)、[graph.py](../src/react_agent/graph.py#L441)。
证据：原工具即使用户仅查询或请求部分商品退货，仍可能被模型调用来准备整单申请。
影响：准备的申请可能不符合用户意图；原人工确认仍会阻挡未确认的实际写入，不等于模型可直接退款。
修复：仅最新 HumanMessage 可以提供申请意图；查询、政策和 ToolMessage 不授予能力；保守拒绝部分退货。图隐藏工具，工具端独立再校验。
复验：无明确意图、部分退货、文档伪造意图不能进入申请；明确整单申请可进入 interrupt，未确认不插入记录；只读模式不能申请。
限制：规则不是通用意图理解模型，可能误拒绝自然表达；补充原因需重复明确整单申请。权限和审批不应转交给 LLM 来“智能判断”。

### SEC-003 · Medium · 请求与会话资源边界不完整

位置：[api_security.py](../src/react_agent/api_security.py#L35)、[api.py](../src/react_agent/api.py#L145)、[run_api.py](../scripts/run_api.py#L17)、[utils.py](../src/react_agent/utils.py#L37)。
证据：Pydantic 文本长度限制发生在 JSON 解析后，不能单独限制大 body；缺少来源/客户速率以及持久会话配额。
影响：输入可消耗解析内存、模型时间和 checkpoint 存储；本地演示的影响范围低于公网多租户系统。
修复：解析前限制累计 body 16 KiB（含分块传输）、接收 body 10 秒、headers 16 KiB；按连接来源 60 次/分钟、客户 20 次/分钟限流。
增加客户 25 会话、会话 50 用户轮次配额；保留全局模型并发门控、整轮 180 秒，限制聊天输出 1024 token。
启动器关闭代理头信任，限制连接并发/keep-alive 和 h11 不完整事件大小。
复验：大 body、分块累计超限、慢 body、非法长度、大 headers、转发 IP 轮换、同客户换 token、历史和会话超限、并发忙均通过。
限制：应用级 headers 检查发生在 HTTP 解析后；h11 和并发配置只是本地传输补充，不等于边缘 WAF、分布式限流或负载 SLA。

### SEC-004 · Medium · 不可信数据与公开输出需要独立边界

位置：[security.py](../src/react_agent/security.py#L22)、[orders.py](../src/react_agent/services/orders.py#L22)、[sufficiency.py](../src/react_agent/knowledge/sufficiency.py#L175)、[responses.py](../src/react_agent/services/responses.py#L334)。
证据：商品/政策可能携带角色指令；模型草稿并非业务事实，也不应直接公开。原来已有确定性最终渲染，本次保留并增加数据入口与反射保护。
修复：检查商品元数据大小和类型，隔离命中角色指令特征的记录/片段；充分性检查前隔离政策，渲染层再次检查。
API 只接受标记为 `validated_renderer` 的最终回答，验证错误仅返回错误码，不回显输入；终端控制字符转义。
复验：伪造未关联工具结果、不存在的 Source 标签、源角色注入、商品角色注入、内部草稿暴露、正常政策扫描对照均通过。
限制：检测器是启发式防御纵深，不能证明任意混淆指令/政策数据投毒都能识别；合法来源也可能不完整或事实错误。引用存在不等于所有语义正确。

### SEC-005 · Low · checkpoint 与访问日志中的内部数据

位置：[security.py](../src/react_agent/security.py#L28)、[api.py](../src/react_agent/api.py#L109)、[run_api.py](../scripts/run_api.py#L23)。
修复：仅对指定 checkpoint 文件创建/打开时设 POSIX `0600`，不改变父目录权限；关闭启动器访问日志以避免意外 query token 进入访问日志。
token 映射在应用状态中使用 SHA-256 摘要键；不将身份/原始 payload 写入错误响应或评估报告。
复验：已有宽权限临时 checkpoint 被收紧；API 查询参数 token 不作为身份；错误不反射输入。
限制：这不是数据库加密；有本机同账户/管理员权限的人仍可读取 `.env`、checkpoint 和内部草稿，不能把 graph state 全量导出给客户。

### SEC-006 · 已知依赖公告 · 定向升级并审计

位置：[pyproject.toml](../pyproject.toml)、[reranker.py](../src/react_agent/knowledge/reranker.py#L32)、[audit_dependencies.py](../scripts/maintenance/audit_dependencies.py)。
首次审计安装的核心 + API 依赖闭包：torch 2.12.0 命中 GHSA-rrmf-rvhw-rf47；setuptools 81.0.0 命中 GHSA-h35f-9h28-mq5c / PYSEC-2026-3447（同一问题的两个记录）。
前者涉及 `torch.jit.script` 内存损坏；未发现本项目将用户代码传入 JIT 的路径，公告严重性不等于本应用存在可远程利用路径。
后者涉及 macOS sdist 非 ASCII 文件排除规则；当前只构建 wheel、不发布 sdist，影响范围也需结合用途判断。
仍定向升级 Conda agent 中 torch 至 2.13.0、setuptools 至 83.0.0，更新安装/构建下限，不更换聊天/embedding/reranker 权重。
再次 OSV 审计完成，覆盖 80 个已安装依赖，无约束冲突和命中公告；版本清单、时间与结果由审计脚本写入本地 `knowledge/results/`。
这只覆盖当时 OSV 收录的 PyPI 公告，不覆盖系统/Ollama/所有 Conda 环境包，也不是依赖哈希锁。
reranker 仍使用固定模型 revision、本地文件、safetensors、`trust_remote_code=False`；不要加载不可信权重或 FAISS 文件。索引 checksums 用于一致性，不提供发布者认证。

## 保留的有效边界

SQL 使用参数绑定与客户作用域条件；订单、邮箱、物流对象逐项检查归属，不允许用户覆盖应用身份。
选单来自最新用户消息或明确上轮指代，不来自 LLM 或检索结果。申请服务事务重查当前订单/政策，并用唯一约束处理并发重复申请。
没有通用 shell/Python/任意 HTTP URL 工具，没有客户端任意模型/数据库路径选择或 API 写入/恢复端点。
本机监听、显式 TrustedHost、没有跨域放开，且不把无 TLS 的本地 Demo 或保留 `/docs` 误报为公网漏洞。

## 复现与交付限制

```bash
conda activate agent
python scripts/evaluation/evaluate_security.py --output knowledge/results/security_boundary_evaluation.json
python scripts/evaluation/evaluate_security.py --real \
  --models ollama/qwen3:4b-instruct-2507-q4_K_M ollama/qwen3:1.7b \
  --output knowledge/results/security_evaluation.json
python scripts/maintenance/audit_dependencies.py --osv
make lint
python scripts/maintenance/check_api.py
```

换模型/提示词/政策/工具/编排后需重新跑相同案例并扩充独立 bad case；不将开发集通过写成“攻击免疫”。
上线仍缺真实身份认证与凭据撤销、依赖哈希锁、可信资源发布、数据保留策略、独立红队及持续压测/可观测性。
当前 API 为只读，写申请仅 CLI 人工确认；不支持部分退货、退款、取消或改址。

本次采用安全最佳实践技能，使检查落实在服务端权限、解析前限制、审批恢复与公开输出，而非只增加安全提示词。
设计参考：[LangGraph 持久任务与重放](https://docs.langchain.com/oss/python/langgraph/functional-api)、[interrupt 恢复行为](https://docs.langchain.com/oss/python/langgraph/interrupts)、[OWASP 提示注入防御](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html)。
依赖查询方法见 [OSV batch API](https://google.github.io/osv.dev/post-v1-querybatch/)；修复公告见 [PyTorch issue](https://github.com/pytorch/pytorch/issues/149623)、[setuptools 安全公告](https://github.com/pypa/setuptools/security/advisories/GHSA-h35f-9h28-mq5c)。
