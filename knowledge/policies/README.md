# Demo Policy Corpus

此目录包含 15 篇虚构商城政策。文档不是实际商户承诺，也不授予业务操作权限。
README 不参与切分或 manifest；不要将开发问题、评估报告或模型回答放入此目录。

## 文档约定

Markdown YAML front matter 必须包含 `policy_id`、`title`、`version`、`effective_date`、
`status`、`source_type`、`is_demo`。只有 active 且按 UTC 日期已生效的文档进入索引。
数据库执行政策与解释文档分开维护；active 文档不表示数据库业务规则已自动同步。

正文使用 `RecursiveCharacterTextSplitter`，不手写标题切分：

- adjacent：1000 字符，重叠 150；选中相邻块后合并，保留 source_chunks。
- parent_child：父块 1800/0，分别再切子块 400/80；只对子块 embedding/rerank，返回不同父块。
- 父子方案默认最多 6000 字符上下文，超预算跳过完整父块，不截断条款；字符预算不等于 token 预算。

## 存储和引用

FAISS 行位置对应 metadata.json 的 chunks 数组位置；父原文位于 parents 数组。
子块 parent_chunk_id 指向父块 chunk_id，这是真实数据关系，不是额外展示引用 ID。
持久标识为 chunk_id；`[Source N]` 仅为当前 prompt 标签，映射在内存中完成。
模型返回 source_labels 与 policy_explanation_supported；程序独立校验本轮来源并推导覆盖状态。
引用存在不证明支持结论。业务主题守卫检查直接条款，模型补充语义判断；两者都不批准实际订单。
卫生封条状态缺失时追问；partial/insufficient 不进入该轮操作分支。

每套 `index.faiss` / `metadata.json` 是独立 generation，由 current.json 原子指向完整文件对。
加载检查校验和、维度、数量、模型、manifest 与 active chunks；不静默回退过期数据。
校验和不是恶意索引安全认证，只加载可信本地生成文件。

## 操作

```bash
python scripts/build_knowledge.py --preview
python scripts/build_knowledge.py
python scripts/build_knowledge.py --query "错发商品的退货运费谁承担？"
python scripts/build_knowledge.py --raw --query "收到商品16天，之前联系过客服，能退吗？"
python scripts/build_knowledge.py --strategy parent_child
python scripts/compare_chunk_strategies.py
python scripts/evaluate_policy_graph.py --output knowledge/policy_graph_evaluation.json
python scripts/evaluate_retrieval_recovery.py
```

默认目录分别是 knowledge/index/policies-faiss 与 policies-faiss-parent-child。
政策或 embedding 配置变化后重建。旧政策 SQLite 和旧 generations 保留回退，不用于当前检索。
开发评估不是独立生产验收，详见项目 README 和 knowledge/business_scenarios.md。
