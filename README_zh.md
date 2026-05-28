<p align="center">
  <h1 align="center">mcp-vcd-analyzer-skill</h1>
  <p align="center">
    把 Verilog <b>VCD</b> 波形封装成 <b>AI Agent 可调用的 Skill</b>
    —— 协议解码、状态机追踪、根因定位、异常检测，全部一条命令搞定，
    无需打开任何波形查看器。
  </p>
</p>

<p align="center">
  <img alt="Version" src="https://img.shields.io/badge/版本-2.0.0-3366cc?style=flat-square">
  <img alt="Python" src="https://img.shields.io/badge/python-3.9+-3366cc?style=flat-square&logo=python&logoColor=white">
  <img alt="License" src="https://img.shields.io/badge/license-MIT-3366cc?style=flat-square">
  <img alt="Tests" src="https://img.shields.io/badge/测试-110/110%20通过-22aa55?style=flat-square">
  <img alt="Skills" src="https://img.shields.io/badge/skills-11-7a3eb6?style=flat-square">
  <img alt="Adapters" src="https://img.shields.io/badge/adapters-MCP%20%C2%B7%20OpenAI%20%C2%B7%20LangChain%20%C2%B7%20REST-7a3eb6?style=flat-square">
</p>

---

## 这是什么？

VCD Analyzer 把一个 Verilog `.vcd` 转储变成 **AI Agent 可以推理的对象**：
每个命令都输出标准化的 JSON envelope，并自带四个 adapter，让同一个 Skill
能在 Claude Desktop（MCP）、GPT-4（Function Calling）、LangChain 或任意 HTTP
客户端中无缝调用。

```text
   Agent（Claude / GPT / Cursor / LangGraph / 自己的脚本）
        │
        ▼  四选一：MCP · OpenAI Function · LangChain · REST · 直接 CLI
   VCD Analyzer Skill 接口  ──  vcd_skill_manifest.json
        │
        ▼
   vcd_analyzer.py        单文件、仅依赖标准库、无需 pip install
        │
        ▼
   磁盘上的 VCD 波形
```

核心引擎仍然是**单个 4600 行的纯 Python 文件，零外部依赖**——丢到任何
有 Python 3.9+ 的机器上就能跑，envelope 协议对 LLM 调用者和终端用户都一视同仁。

## 11 个 Skill

| Skill | 解决什么问题 | CLI 命令 |
|:------|:-------------|:---------|
| `info` | 这个文件里有什么？（时间精度、scope、信号数、时间跨度） | `info` |
| `list` | 哪些信号匹配这个模式？ | `list` |
| `dump` | T1 到 T2 之间发生了什么？ | `dump` |
| `summary` | 时间窗口内的逐信号统计：活跃/静态、边沿、变化次数 | `summary` |
| `snapshot` | T 时刻所有信号的瞬时值是什么？ | `snapshot` |
| `compare` | T1 和 T2 之间哪些信号变了？（两个快照的 diff） | `compare` |
| `search` | `valid=1 && ready=1` 何时同时成立？`state` 何时真的变化？ | `search` |
| `protocol_decode` | 解码 AXI4 / APB / UART / SPI 事务 + 协议违例 | `protocol-decode` |
| `fsm_trace` | 提取状态机迁移，检测 stuck state | `fsm-trace` |
| `causality` | 哪些信号可能导致了这次变化？ | `causality` |
| `anomaly_detect` | 找出 stuck signal、glitch、亚稳态、总线竞争 | `anomaly-detect` |

前 7 个是**基础查询**，相当于波形上的 `grep` / `jq`；后 4 个是**高层分析**，
理解总线协议、状态机和时序关系。

## 快速开始

### 作为 AI Agent Skill

**发现能力清单**

```bash
python vcd_analyzer.py --skill-manifest        # 完整 manifest（JSON）
python vcd_analyzer.py --skill-info causality  # 单个能力条目
```

**调用一个 Skill（返回标准 envelope）**

```bash
python vcd_analyzer.py causality sim.vcd \
       --effect error_flag --at 17.5us --window 100ns --json
```

```json
{
  "status": "success",
  "skill": "causality",
  "execution_time_ms": 14,
  "input": { "file": "sim.vcd", "effect": "error_flag", "at": "17.5us" },
  "result": {
    "effect": { "signal": "error_flag", "value": "1" },
    "potential_causes": [
      { "signal": "fifo_full", "delta_ns": 30, "correlation": 0.95,
        "confidence": "high" }
    ],
    "causal_chain": [ ... ]
  },
  "metadata": { "vcd_file_size": 12500000, "signals_matched": 8,
                "analyzer_version": "2.0.0",
                "time_range_analyzed": ["17.4us", "17.5us"] },
  "suggestions": [
    "与 fifo_full 高度相关（95%），最可能的根因",
    "建议用 fsm-trace 确认控制器是否正确处理了 fifo_full"
  ]
}
```

### 作为终端用户

```bash
# 文件里有什么？
python vcd_analyzer.py info sim.vcd

# valid=1 且 ready=1 同时成立的时刻？
python vcd_analyzer.py search sim.vcd --condition "valid=1,ready=1" --show data

# 解码 100us 内的 AXI4 流量，并标记协议违例
python vcd_analyzer.py protocol-decode sim.vcd \
       --protocol axi4 --signals "m_axi_*" --begin 17us --end 18us

# 在窗口内查找异常（stuck signal、glitch、x/z 值）
python vcd_analyzer.py anomaly-detect sim.vcd --filter axi --begin 0 --end 1ms
```

所有命令都支持 `--begin` / `--end`（带单位 `fs` / `ps` / `ns` / `us` /
`ms` / `s`），并通过 `--json` 输出结构化 envelope。

## AI Agent 集成

四个 adapter 开箱即用，按你的技术栈挑一个：

| Adapter | 适用场景 | 安装命令 |
|---------|----------|----------|
| **MCP Server** | Claude Desktop · Claude Code · Continue · Cline | `pip install mcp` |
| **OpenAI Function Calling** | GPT-4o · Claude（Anthropic API） · Gemini · 任意 function-calling LLM | （仅 schema 时只需标准库） |
| **LangChain Tools** | LangChain agents · LangGraph | `pip install langchain pydantic` |
| **REST API** | 跨语言客户端、云端 Agent | `pip install flask` |

四个 adapter 对外暴露同样的 11 个 Skill，envelope 契约完全一致。

**MCP 示例** —— 加入 `claude_desktop_config.json`：

```json
{
  "mcpServers": {
    "vcd-analyzer": {
      "command": "python",
      "args": ["/绝对路径/vcd_integrations/mcp/server.py"]
    }
  }
}
```

**OpenAI Function Calling 示例** —— manifest 自动生成
[`functions.json`](vcd_integrations/openai/functions.json) 传给 OpenAI API，
[executor](vcd_integrations/openai/executor.py) 负责真正调用 CLI 并把
envelope 原样返回。

**LangChain 示例** —— Tool 类在 import 时直接从 manifest 派生，新增 Skill
**零 LangChain 侧代码改动**：

```python
from vcd_integrations.langchain_tools import build_tools
tools = build_tools()   # 每个 Skill 一个 BaseTool，共 11 个
```

**REST 示例**：

```bash
python vcd_integrations/rest_api/server.py --port 5000
curl -X POST http://localhost:5000/api/v1/causality \
     -H 'Content-Type: application/json' \
     -d '{"file":"sim.vcd","effect":"error_flag","at":"17.5us"}'
```

完整配置指南：[docs/agent_adapters.md](docs/agent_adapters.md)。

## Skill Envelope

每个 Skill 返回相同的 JSON 形态，Agent（和人类）只需要写一次解析逻辑：

```jsonc
{
  "status": "success" | "error",
  "skill":  "<name>",
  "execution_time_ms": 14,
  "input":  { /* 输入回显，方便审计 */ },
  "result": { /* Skill 专属结果 */ },              // success 时
  "metadata": {
    "vcd_file_size": 12500000,
    "analyzer_version": "2.0.0",
    "signals_matched": 8,
    "time_range_analyzed": ["17.4us", "17.5us"]
  },
  "error":  { "code": "...", "message": "...", "details": {} },  // error 时
  "suggestions": [ "给 Agent 的下一步建议", "..." ]
}
```

错误码在 manifest 里枚举：`FILE_NOT_FOUND`、`PARSE_ERROR`、
`INVALID_PROTOCOL`、`SIGNAL_NOT_FOUND`、`INVALID_TIME_RANGE`、
`INVALID_ARGUMENT`、`INSUFFICIENT_DATA`、`RESOURCE_LIMIT`、
`INTERNAL_ERROR`。

完整规范：[docs/skill_envelope.md](docs/skill_envelope.md)。

## 项目结构

```
vcd_analyzer.py                单文件核心引擎（仅依赖标准库）
vcd_skill_manifest.json        11 个 Skill 的唯一真理来源
vcd_integrations/              AI Agent 适配器（pip 选装）
├── mcp/                       MCP Server（stdio 传输）
├── openai/                    Function Calling schema + 执行器
├── langchain_tools.py         manifest 驱动的 BaseTool 工厂
└── rest_api/                  Flask HTTP/JSON 层
docs/                          单 Skill 参考 + 集成指南
verify/                        110 个测试覆盖 helpers / parser /
                               CLI / envelope shape / 4 个 adapter
verify/fixtures/               脱敏 VCD 测试波形
verify/samples/                真实 GitHub VCD 样本
version_notes/                 每个版本的变更说明
plan.md                        多阶段升级路线图
```

## 测试

```bash
# 完整 pytest 套件 —— 110 个测试覆盖每个命令、每个 adapter
python -m pytest verify/ -v

# 子集：envelope / manifest 契约
python verify/test_skill_envelope.py

# 子集：adapter glue（无需 mcp / openai / langchain 安装即可跑）
python verify/test_integrations.py
```

CI 在 Ubuntu / Windows / macOS × Python 3.9-3.12 矩阵下运行，并通过
`diff` 校验 `vcd_integrations/openai/functions.json` 与 manifest
保持同步。

## 文档

| 文档 | 内容 |
|:-----|:-----|
| [docs/skill_envelope.md](docs/skill_envelope.md) | 标准 JSON envelope + 错误码 |
| [docs/agent_adapters.md](docs/agent_adapters.md) | MCP / OpenAI / LangChain / REST 接入指南 |
| [docs/protocol_decode.md](docs/protocol_decode.md) | AXI4 解码器细节 |
| [docs/protocol_decode_apb_uart_spi.md](docs/protocol_decode_apb_uart_spi.md) | APB / UART / SPI 解码器 |
| [docs/fsm_trace.md](docs/fsm_trace.md) | 状态机提取与异常检测 |
| [docs/causality.md](docs/causality.md) | 时序相关性 + 因果链路打分 |
| [docs/anomaly_detect.md](docs/anomaly_detect.md) | Stuck / glitch / 亚稳态 / 总线竞争 |
| [plan.md](plan.md) | Phase 1-4 路线图与状态 |

## 版本历史

详细变更见 [version_notes/](version_notes/)。

| 版本 | 亮点 |
|:------|:-----|
| `2.0.0` | **11 个命令统一为 AI Agent Skill**：标准 envelope、manifest、4 个 adapter（MCP / OpenAI / LangChain / REST）、manifest 驱动的 adapter glue |
| `1.3.9` | 消除数据扫描路径中的重复解析代码 |
| `1.3.8` | 加固输入校验和错误报告 |
| `1.3.7` | 修复字面量总线范围通配和转义标识符作用域 |
| `1.3.0` | 基于条件和观察信号重新设计搜索 |
| `1.0.0` | 首次公开发布 |

## 许可证

MIT —— 详见 [LICENSE](LICENSE)。&copy; 2026 neveltyc

[English](README.md)
