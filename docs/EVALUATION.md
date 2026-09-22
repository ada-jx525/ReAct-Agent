# Evaluation

本项目把评估分成三层：确定性单元测试、固定业务回归和安全边界检查。所有数据均为合成数据；
结果用于发现回归，不代表生产准确率、线上 SLA 或渗透测试认证。

## 快速验证

```bash
make test
make lint
python scripts/evaluation/evaluate_retrieval.py
```

不依赖本地大模型的 CI 检查包括 Python 3.11/3.12、Ruff、格式、13 个订单/路由单元测试、
wheel 构建和包内容校验。

## 业务回归集

固定业务集位于 `knowledge/evaluation/business_acceptance_v1.json`，包含 26 个场景：

- 订单查询、订单号规范化与不存在订单；
- 当前客户身份隔离和跨客户请求拒绝；
- 物流查询、退货资格三态和多轮选单；
- 退货政策、证据不足与未知能力；
- 人工批准/拒绝后的事务写入；
- 模型故障和检索故障的安全降级。

运行：

```bash
python -u scripts/evaluation/evaluate_business.py
```

默认报告写入被 Git 忽略的 `knowledge/results/business_acceptance.json`。测试使用临时订单副本与
checkpoint；只有批准场景会在临时副本写入一条申请，并在结束时校验原始数据库和政策未变。

当前固定回归集在 `ollama/qwen3:4b-instruct-2507-q4_K_M`、`adjacent` 索引策略下报告
26/26。该集合已参与实现调优，因此只能说明已知行为没有回退，不能估计陌生问题上的泛化能力。

## 历史基线与修复方向

首次完整业务基线的自动断言为 19/26，人工复核为 20/26。主要失败并不是缺少更多政策文档，
而是以下编排问题：

- 物流号曾被模型错误路由到订单工具；
- 跨客户请求虽然未泄露数据，但追问内容不正确；
- 有证据支持否定答案时，充分性判断仍可能过度拒答；
- 子问题切分丢失原问题上下文；
- 模型故障没有稳定的用户可见降级路径。

修复策略是把身份、实体规范化、高置信度工具路由和写入授权放到确定性代码中；LLM 负责有限枚举的
语义理解和开放式回答，不承担本可由服务端可靠验证的权限与参数工作。

## RAG 评估

```bash
python scripts/evaluation/evaluate_retrieval.py
python scripts/evaluation/evaluate_retrieval_recovery.py
```

检索评估检查目标来源与字面条款覆盖，不等同于生成答案正确率。漏召回修复采用受治理的主题扩展：
当原问题结果缺少期限、退款阶段、卫生封条或运费责任等必要主题时，最多执行两次双语补检索；扩展词
不包含答案、不指定目标文件，新增片段必须实际减少缺失主题，并继续受来源、版本和上下文预算约束。

开发集曾观察到：

| 策略 | 修复前完整覆盖 | 修复后完整覆盖 |
| --- | ---: | ---: |
| adjacent | 6/12 | 10/12 |
| parent-child | 8/12 | 11/12 |

这些数据来自参与调优的开发集，不能包装成通用召回率。更换 embedding、reranker、切分策略或政策后，
必须重新运行固定变量对比，并保留尚未参与调优的 holdout。

## 泛化探针

`knowledge/evaluation/` 还包含多语言和组合问题探针。首次运行后应冻结；一旦根据失败项修改代码，
该版本就降级为回归集，需要另建未看过的评估集。

```bash
python -u scripts/evaluation/evaluate_business.py \
  --dataset knowledge/evaluation/multilingual_generalization_probe_v1.json \
  --output knowledge/results/multilingual_generalization_probe_v1_result.json
```

## 安全评估

安全回归覆盖客户隔离、提示注入、只读能力、人工确认、checkpoint 恢复、重放、资源限制和输出反射。
具体威胁模型、已修复问题和剩余风险见 [Security design](SECURITY_DESIGN.md)。

```bash
python scripts/evaluation/evaluate_security.py \
  --output knowledge/results/security_boundary_evaluation.json
python scripts/maintenance/check_api.py
```

安全用例通过只说明已知边界未回退，不能声称系统“无法被攻击”。

## 指标解释边界

- 字面锚点不理解否定、条件和引用上下文，需要人工复核最终答案。
- 安全拒答只有在确实缺少权限或证据时才算成功；有可靠证据却拒答属于能力失败。
- 冷启动、模型下载和本机并行任务会污染延迟，开发机耗时不能作为服务 SLA。
- 本地 checkpoint 和评估报告可能包含对话或模型草稿，统一写入忽略目录，不提交 Git。
