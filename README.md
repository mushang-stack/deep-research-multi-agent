# AI-PM-Agent · 深度研究型多 Agent 系统

基于 DeepSeek 的**模型自驱**多 Agent 编排系统:接受研究问题 → 规划 / 检索 / 验证 / 撰写 → 产出带引用的研究报告。**自研 harness**(非现成 SDK),核心原语 `AgentLoop` + `ToolRegistry` + 重试 / 兜底解析 / escalation。

> 详细设计见 [spec](docs/superpowers/specs/2026-08-08-deep-research-multi-agent-design.md)。

## 当前里程碑:M1 地基 ✅

已完成:harness 原语(AgentLoop / ToolRegistry / robustness)、LLM 客户端(DeepSeek 生成 + GLM-5.2 裁判)、博查搜索、trafilatura 提取、Pydantic 数据结构。全部单测覆盖,不烧 API。

## 快速开始

```bash
python -m venv .venv && source .venv/Scripts/activate   # Windows Git Bash
pip install -r requirements.txt
cp .env.example .env   # 填入 DEEPSEEK / GLM / BOCHA key(M1 测试不需要)
pytest                  # 跑全部单测
```

## 后续里程碑

- **M2** 四个 agent(orchestrator / researcher / verifier / writer)+ 子 agent 派发 + `main.py` 真实链路
- **M3** 评估体系(9 维指标 + 基准集 + 基线对比 + 上线门槛)
- **M4** Streamlit 实时编排可视化
