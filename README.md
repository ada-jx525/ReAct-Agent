# Local Customer Support Agent

一个基于 LangGraph、Ollama 和 FAISS 构建的本地客服 Agent Demo。

项目使用合成订单和政策数据，支持订单查询、物流查询、退货资格检查、政策 RAG，以及需要人工确认的退货申请流程。

## 核心能力

- ReAct 工具调用与 LangGraph 工作流编排
- 客户身份隔离和服务端工具参数校验
- FAISS 检索、CrossEncoder 重排与证据充分性判断
- 退货申请人工确认、SQLite checkpoint 和事务写入
- 本地 Ollama 模型，不需要云端 API Key

## 项目结构

```text
.
├── src/react_agent/
│   ├── graph.py            # Agent 工作流与路由
│   ├── tools.py            # Agent 工具定义
│   ├── services/           # 订单、物流和退货业务服务
│   ├── data/               # SQLite 数据层与模拟数据初始化
│   └── knowledge/          # FAISS、rerank 和证据检查
├── knowledge/
│   ├── policies/           # 模拟客服政策文档
│   └── evaluation/         # 固定业务评估集
├── scripts/
│   ├── run_local.py        # 本地命令行 Demo
│   ├── run_api.py          # FastAPI 启动入口
│   └── build_knowledge.py  # 构建政策索引
├── tests/                  # 单元测试
└── docs/                   # 架构、评估和安全设计
```

## 快速启动

要求 Python 3.11+，并提前安装 [Ollama](https://ollama.com/)。

```bash
git clone https://github.com/ada-jx525/ReAct-Agent.git
cd ReAct-Agent

python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev,api]'
cp .env.example .env
```

准备本地模型：

```bash
ollama pull qwen3:1.7b
ollama pull embeddinggemma
python scripts/prepare_reranker.py
```

初始化模拟订单并构建知识索引：

```bash
make setup
python scripts/build_knowledge.py
```

启动命令行 Demo：

```bash
python scripts/run_local.py --customer-email james@example.com
```

可以尝试：

```text
我的订单有哪些？
查询 ORD-1001 的商品和总价。
查询 FDX-78901234 的物流状态。
耳塞拆封后还能退货吗？
帮我提交 ORD-1001 的整单退货申请。
```

启动只读 API：

```bash
python scripts/run_api.py
```

## 开发检查

```bash
make test
make lint
```

所有订单、物流、客户和政策均为合成数据。本项目用于 Agent 学习与作品展示，不是生产客服系统。

设计细节见 [Architecture](docs/ARCHITECTURE.md)，评估说明见 [Evaluation](docs/EVALUATION.md)，安全边界见 [Security](SECURITY.md)。

## License

本项目基于 LangChain `react-agent` 模板扩展，采用 MIT License。来源和第三方模型说明见 [NOTICE.md](NOTICE.md)。
