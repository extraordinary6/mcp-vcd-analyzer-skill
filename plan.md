# VCD Analyzer - AI Agent Skill Interface 升级计划

## Phase 1 + Phase 2 完成状态总结 (2026-05-27)

**所有 4 个核心 Skill 已实现并通过测试 + Phase 2 标准化接口已上线**：

| Skill | 命令 | 状态 | 测试数 |
|-------|------|------|--------|
| Protocol Decode (AXI4) | `protocol-decode --protocol axi4` | ✅ 完成 | 1 |
| Protocol Decode (APB) | `protocol-decode --protocol apb` | ✅ 完成 | 3 |
| Protocol Decode (UART) | `protocol-decode --protocol uart` | ✅ 完成 | 2 |
| Protocol Decode (SPI) | `protocol-decode --protocol spi` | ✅ 完成 | 3 |
| FSM Trace | `fsm-trace` | ✅ 完成 | 5 |
| Causality Analysis | `causality` | ✅ 完成 | 7 |
| Anomaly Detection | `anomaly-detect` | ✅ 完成 | 10 |
| 通用 protocol-decode | (含错误处理) | ✅ 完成 | 2 |
| **Skill Envelope / Manifest / Error** | `--skill-manifest`, `--skill-info`, 标准化 envelope | ✅ 完成 | 12 |

**总测试数**：45 个 Skill 测试 + 36 个核心测试 = **81 个测试，全部通过**
（pytest 收集总数 86 个，含 external sample 测试）

**Phase 2 新增能力**:
- 统一 JSON envelope（`status` / `skill` / `execution_time_ms` / `input` / `result` / `metadata` / `suggestions`）
- 结构化错误（9 类标准错误码 + `details` 字典）
- Skill manifest 自描述（`--skill-manifest`、`--skill-info <name>`）
- 错误退出码 != 0（Agent 可不解析 JSON 即可检测失败）

**CI 集成**：GitHub Actions 矩阵测试（Ubuntu/Windows/macOS × Python 3.9-3.12）
+ Skill manifest CLI 验证步骤

---

## 项目定位

将 VCD Analyzer 从"波形查询工具"升级为"AI Agent 可调用的智能波形分析 Skill"。

**核心理念**：
- 我们**不做** Agent：不实现对话系统、不做自然语言理解
- 我们**只做** Skill：提供标准化的工具接口，让其他 AI Agent（Claude Code、GPT-4、Cursor 等）方便调用
- 从"数据查询"升级为"语义理解"：协议解码、因果分析、异常检测

---

## 架构设计

```
AI Agent (Claude/GPT/Cursor/etc)
    ↓ 调用标准接口
VCD Analyzer Skill Interface (统一 JSON API)
    ↓ 执行分析
VCD Analysis Engine (协议解码、因果分析、异常检测)
    ↓ 解析数据
VCD Parser (现有核心，保持单文件)
```

**设计原则**：
1. **保持核心简洁**：`vcd_analyzer.py` 仍是单文件、零依赖
2. **JSON First**：所有 Skill 输出标准化 JSON，易于 Agent 解析
3. **自描述**：通过 manifest 让 Agent 知道"我能做什么"
4. **渐进式增强**：基础功能不变，高级 Skill 作为新子命令添加
5. **Agent 友好**：输出包含 `suggestions` 引导 Agent 下一步操作

---

## Phase 1: 核心 Skill 实现（1-2 月）

### 1.1 协议解码 Skill

**命令**：`protocol-decode`

```bash
python vcd_analyzer.py protocol-decode <file> --protocol <type> --signals <pattern> [--begin T] [--end T] --json
```

**支持的协议**：
- AXI4 (AXI4-Lite, AXI4-Full, AXI4-Stream)
- APB (APB2, APB3)
- UART
- SPI

**输出示例**：
```json
{
  "status": "success",
  "skill": "protocol_decode",
  "execution_time_ms": 234,
  "input": {
    "file": "sim.vcd",
    "protocol": "axi4",
    "signals": "m_axi_*"
  },
  "result": {
    "transactions": [
      {
        "id": 0,
        "type": "write",
        "start_time": "17.5us",
        "end_time": "17.8us",
        "addr": "0x1000",
        "data": ["0xDEADBEEF", "0xCAFEBABE"],
        "burst_len": 2,
        "burst_type": "INCR",
        "status": "OKAY"
      }
    ],
    "violations": [
      {
        "type": "protocol_violation",
        "time": "18.2us",
        "severity": "error",
        "description": "WVALID asserted without prior AWVALID"
      }
    ],
    "statistics": {
      "total_transactions": 15,
      "read_count": 8,
      "write_count": 7,
      "avg_latency": "120ns",
      "bandwidth_utilization": 0.65
    }
  },
  "suggestions": [
    "Found 1 protocol violation at 18.2us, use --verbose for detailed timing",
    "Low bandwidth utilization (65%), check for stalls on READY signals"
  ]
}
```


**实现要点**：
- 自动识别信号角色（AWVALID/AWREADY/AWADDR 等）
- 重建完整的握手时序
- 检测协议违例（时序错误、非法状态组合）
- 计算性能指标（带宽、延迟、利用率）

---

### 1.2 状态机追踪 Skill

**命令**：`fsm-trace`

```bash
python vcd_analyzer.py fsm-trace <file> --state <signal> [--triggers <signals>] [--begin T] [--end T] --json
```

**输出示例**：
```json
{
  "status": "success",
  "skill": "fsm_trace",
  "result": {
    "states": [
      {"value": "0x0", "name": "IDLE"},
      {"value": "0x1", "name": "REQ"},
      {"value": "0x2", "name": "WAIT"},
      {"value": "0x3", "name": "ACK"},
      {"value": "0xF", "name": "ERROR"}
    ],
    "transitions": [
      {
        "from": "IDLE",
        "to": "REQ",
        "time": "10ns",
        "trigger": "valid=1",
        "duration_in_from": "50ns"
      },
      {
        "from": "WAIT",
        "to": "ERROR",
        "time": "1.2ms",
        "trigger": "timeout",
        "duration_in_from": "500us"
      }
    ],
    "anomalies": [
      {
        "type": "stuck_state",
        "state": "WAIT",
        "time": "700us",
        "duration": "500us",
        "severity": "warning",
        "description": "State machine stuck in WAIT for 500us (threshold: 100us)"
      },
      {
        "type": "unexpected_transition",
        "from": "WAIT",
        "to": "ERROR",
        "time": "1.2ms",
        "severity": "error",
        "description": "Unexpected transition (not in normal flow)"
      }
    ]
  },
  "suggestions": [
    "State machine stuck in WAIT state for 500us, check timeout logic",
    "Unexpected transition to ERROR state, use causality analysis to find root cause"
  ]
}
```

**实现要点**：
- 自动识别状态编码（枚举值或 one-hot）
- 构建状态转换图
- 检测异常：stuck state、dead state、unexpected transition
- 计算每个状态的停留时间统计


---

### 1.3 因果分析 Skill

**命令**：`causality`

```bash
python vcd_analyzer.py causality <file> --effect <signal> --at <time> [--window <duration>] --json
```

**输出示例**：
```json
{
  "status": "success",
  "skill": "causality",
  "result": {
    "effect": {
      "signal": "error_flag",
      "time": "17.55us",
      "value": "1"
    },
    "potential_causes": [
      {
        "signal": "fifo_full",
        "change_time": "17.52us",
        "delta": "30ns",
        "value": "1",
        "correlation": 0.95,
        "confidence": "high",
        "pattern": "fifo_full=1 → error_flag=1 (observed in 19/20 occurrences)"
      },
      {
        "signal": "timeout_counter",
        "change_time": "17.54us",
        "delta": "10ns",
        "value": "1024",
        "correlation": 0.87,
        "confidence": "medium",
        "pattern": "timeout_counter>1000 → error_flag=1"
      }
    ],
    "causal_chain": [
      {"signal": "input_valid", "time": "17.50us", "value": "1"},
      {"signal": "fifo_write", "time": "17.51us", "value": "1"},
      {"signal": "fifo_full", "time": "17.52us", "value": "1"},
      {"signal": "error_flag", "time": "17.55us", "value": "1"}
    ]
  },
  "suggestions": [
    "High correlation with fifo_full (95%), likely root cause",
    "Use 'fsm-trace' to check if state machine handled FIFO full correctly"
  ]
}
```

**实现要点**：
- 在时间窗口内查找所有信号变化
- 计算时间相关性（temporal correlation）
- 识别因果链路（A → B → C）
- 基于历史模式匹配提高置信度

---

### 1.4 异常检测 Skill

**命令**：`anomaly-detect`

```bash
python vcd_analyzer.py anomaly-detect <file> [--filter <pattern>] [--begin T] [--end T] --json
```

**输出示例**：
```json
{
  "status": "success",
  "skill": "anomaly_detect",
  "result": {
    "anomalies": [
      {
        "type": "stuck_signal",
        "signal": "clk_div",
        "time_range": ["1ms", "2ms"],
        "severity": "critical",
        "description": "Clock signal stuck at 0 for 1ms"
      },
      {
        "type": "glitch",
        "signal": "data_valid",
        "time": "17.523us",
        "duration": "2ns",
        "severity": "warning",
        "description": "Pulse width 2ns < minimum expected 10ns"
      },
      {
        "type": "metastability",
        "signal": "sync_flag",
        "time": "18.1us",
        "value": "x",
        "severity": "error",
        "description": "Unknown value detected on synchronizer output"
      },
      {
        "type": "bus_contention",
        "signal": "data_bus[7:0]",
        "time": "19.2us",
        "value": "8'bxxxx_xxxx",
        "severity": "error",
        "description": "Bus contention detected (all bits unknown)"
      }
    ],
    "summary": {
      "total_anomalies": 4,
      "critical": 1,
      "error": 2,
      "warning": 1
    }
  },
  "suggestions": [
    "Critical: Clock stopped at 1ms, check clock generation logic",
    "2 metastability issues detected, review CDC (Clock Domain Crossing) design"
  ]
}
```

**实现要点**：
- Stuck signal：长时间无变化
- Glitch：脉冲宽度异常
- Metastability：x/z 值检测
- Bus contention：多驱动冲突
- 可配置的阈值和规则


---

## Phase 2: Skill 接口标准化（2-3 周）

### 2.1 统一 JSON 输出格式

所有 Skill 遵循统一的响应结构：

```json
{
  "status": "success | error",
  "skill": "<skill_name>",
  "execution_time_ms": 234,
  "input": {
    // 输入参数的回显
  },
  "result": {
    // 具体的分析结果
  },
  "metadata": {
    "vcd_file_size": "12.5 MB",
    "time_range_analyzed": ["0ns", "1ms"],
    "signals_matched": 15,
    "analyzer_version": "2.0.0"
  },
  "suggestions": [
    // 给 Agent 的下一步建议
  ],
  "error": {
    // 仅在 status=error 时存在
    "code": "INVALID_PROTOCOL",
    "message": "Unsupported protocol type: 'i2c'",
    "details": "Supported protocols: axi4, apb, uart, spi"
  }
}
```

**关键字段说明**：
- `status`：执行状态，便于 Agent 快速判断
- `suggestions`：引导 Agent 下一步操作（最重要！）
- `metadata`：上下文信息，帮助 Agent 理解分析范围
- `error`：结构化错误信息，便于 Agent 处理异常

---

### 2.2 Skill Manifest 文件

创建 `vcd_skill_manifest.json`，让 Agent 自动发现能力：

```json
{
  "name": "vcd_analyzer",
  "version": "2.0.0",
  "description": "VCD waveform analysis tool for RTL debug",
  "repository": "https://github.com/yourusername/vcd_analyzer",
  "capabilities": [
    {
      "skill": "protocol_decode",
      "description": "Decode bus protocol transactions (AXI/APB/UART/SPI)",
      "category": "protocol_analysis",
      "input_schema": {
        "type": "object",
        "properties": {
          "file": {
            "type": "string",
            "description": "Path to VCD file"
          },
          "protocol": {
            "type": "string",
            "enum": ["axi4", "apb", "uart", "spi"],
            "description": "Protocol type to decode"
          },
          "signals": {
            "type": "string",
            "description": "Signal pattern (e.g., 'm_axi_*')"
          },
          "time_range": {
            "type": "object",
            "properties": {
              "begin": {"type": "string"},
              "end": {"type": "string"}
            }
          }
        },
        "required": ["file", "protocol"]
      },
      "output_schema": {
        "transactions": "array of transaction objects",
        "violations": "array of protocol violations",
        "statistics": "bandwidth, latency, utilization"
      },
      "example": "python vcd_analyzer.py protocol-decode sim.vcd --protocol axi4 --signals m_axi_* --json"
    },
    {
      "skill": "fsm_trace",
      "description": "Extract state machine transitions and detect anomalies",
      "category": "behavioral_analysis",
      "input_schema": {
        "type": "object",
        "properties": {
          "file": {"type": "string"},
          "state_signal": {"type": "string"},
          "trigger_signals": {
            "type": "array",
            "items": {"type": "string"}
          }
        },
        "required": ["file", "state_signal"]
      }
    },
    {
      "skill": "causality",
      "description": "Find potential causes for a signal change",
      "category": "root_cause_analysis",
      "input_schema": {
        "type": "object",
        "properties": {
          "file": {"type": "string"},
          "effect_signal": {"type": "string"},
          "effect_time": {"type": "string"},
          "search_window": {"type": "string"}
        },
        "required": ["file", "effect_signal", "effect_time"]
      }
    },
    {
      "skill": "anomaly_detect",
      "description": "Automatically detect waveform anomalies",
      "category": "quality_check",
      "input_schema": {
        "type": "object",
        "properties": {
          "file": {"type": "string"},
          "signal_filter": {"type": "string"},
          "time_range": {"type": "object"}
        },
        "required": ["file"]
      }
    }
  ]
}
```

**访问方式**：
```bash
# 输出 manifest
python vcd_analyzer.py --skill-manifest

# 输出特定 skill 的详细信息
python vcd_analyzer.py --skill-info protocol_decode
```


---

### 2.3 错误处理标准化

所有错误都返回结构化信息：

```json
{
  "status": "error",
  "skill": "protocol_decode",
  "error": {
    "code": "SIGNAL_NOT_FOUND",
    "message": "No signals matched pattern 'm_axi_*'",
    "details": {
      "pattern": "m_axi_*",
      "available_signals": ["s_axi_awvalid", "s_axi_awready", "..."],
      "suggestion": "Did you mean 's_axi_*'?"
    }
  }
}
```

**错误码分类**：
- `FILE_NOT_FOUND`：VCD 文件不存在
- `PARSE_ERROR`：VCD 格式错误
- `SIGNAL_NOT_FOUND`：信号匹配失败
- `INVALID_TIME_RANGE`：时间范围无效
- `INVALID_PROTOCOL`：不支持的协议
- `INSUFFICIENT_DATA`：数据不足以完成分析

---

## Phase 3: 多 Agent 适配器（1 月）

### 3.1 MCP (Model Context Protocol) Server

为 Claude Code 等支持 MCP 的 Agent 提供原生集成：

**文件结构**：
```
vcd_integrations/
├── mcp/
│   ├── server.py           # MCP Server 实现
│   ├── config.json         # Claude Desktop 配置示例
│   └── README.md
```

**server.py 核心代码**：
```python
from mcp.server import Server
from mcp.types import Tool, TextContent
import subprocess
import json

server = Server("vcd-analyzer")

@server.list_tools()
async def list_tools():
    return [
        Tool(
            name="vcd_protocol_decode",
            description="Decode bus protocol transactions from VCD waveform",
            inputSchema={
                "type": "object",
                "properties": {
                    "file": {"type": "string"},
                    "protocol": {"type": "string", "enum": ["axi4", "apb", "uart", "spi"]},
                    "signals": {"type": "string"}
                },
                "required": ["file", "protocol"]
            }
        ),
        Tool(
            name="vcd_fsm_trace",
            description="Extract state machine transitions and detect anomalies",
            inputSchema={
                "type": "object",
                "properties": {
                    "file": {"type": "string"},
                    "state_signal": {"type": "string"}
                },
                "required": ["file", "state_signal"]
            }
        ),
        Tool(
            name="vcd_causality_analysis",
            description="Find potential causes for a signal change",
            inputSchema={
                "type": "object",
                "properties": {
                    "file": {"type": "string"},
                    "effect_signal": {"type": "string"},
                    "effect_time": {"type": "string"}
                },
                "required": ["file", "effect_signal", "effect_time"]
            }
        ),
        Tool(
            name="vcd_anomaly_detect",
            description="Automatically detect waveform anomalies",
            inputSchema={
                "type": "object",
                "properties": {
                    "file": {"type": "string"},
                    "signal_filter": {"type": "string"}
                },
                "required": ["file"]
            }
        )
    ]

@server.call_tool()
async def call_tool(name: str, arguments: dict):
    # 映射 tool name 到命令
    command_map = {
        "vcd_protocol_decode": "protocol-decode",
        "vcd_fsm_trace": "fsm-trace",
        "vcd_causality_analysis": "causality",
        "vcd_anomaly_detect": "anomaly-detect"
    }
    
    cmd = ["python", "vcd_analyzer.py", command_map[name], arguments["file"], "--json"]
    
    # 添加参数
    if name == "vcd_protocol_decode":
        cmd.extend(["--protocol", arguments["protocol"]])
        if "signals" in arguments:
            cmd.extend(["--signals", arguments["signals"]])
    elif name == "vcd_fsm_trace":
        cmd.extend(["--state", arguments["state_signal"]])
    elif name == "vcd_causality_analysis":
        cmd.extend(["--effect", arguments["effect_signal"]])
        cmd.extend(["--at", arguments["effect_time"]])
    elif name == "vcd_anomaly_detect":
        if "signal_filter" in arguments:
            cmd.extend(["--filter", arguments["signal_filter"]])
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    return [TextContent(type="text", text=result.stdout)]
```

**Claude Desktop 配置** (`config.json`):
```json
{
  "mcpServers": {
    "vcd-analyzer": {
      "command": "python",
      "args": ["path/to/vcd_integrations/mcp/server.py"]
    }
  }
}
```

**使用示例**：
```
User: 分析 sim.vcd 中的 AXI 传输
Claude: [自动调用 vcd_protocol_decode tool]
        发现 15 个 AXI 传输，其中 1 个协议违例...
```


---

### 3.2 OpenAI Function Calling 格式

为 GPT-4/GPT-4o 等模型提供 Function Calling 接口：

**文件**：`vcd_integrations/openai/functions.json`

```json
[
  {
    "name": "vcd_protocol_decode",
    "description": "Decode bus protocol transactions (AXI/APB/UART/SPI) from VCD waveform file. Returns transaction details, protocol violations, and performance statistics.",
    "parameters": {
      "type": "object",
      "properties": {
        "file": {
          "type": "string",
          "description": "Path to VCD file"
        },
        "protocol": {
          "type": "string",
          "enum": ["axi4", "apb", "uart", "spi"],
          "description": "Protocol type to decode"
        },
        "signals": {
          "type": "string",
          "description": "Signal pattern to match (e.g., 'm_axi_*', 's_apb_*')"
        },
        "time_begin": {
          "type": "string",
          "description": "Start time (e.g., '10ns', '1us')"
        },
        "time_end": {
          "type": "string",
          "description": "End time (e.g., '100ns', '10us')"
        }
      },
      "required": ["file", "protocol"]
    }
  },
  {
    "name": "vcd_fsm_trace",
    "description": "Extract state machine transitions from VCD waveform. Detects stuck states, unexpected transitions, and provides state duration statistics.",
    "parameters": {
      "type": "object",
      "properties": {
        "file": {"type": "string", "description": "Path to VCD file"},
        "state_signal": {"type": "string", "description": "State signal name (e.g., 'state[3:0]')"},
        "trigger_signals": {
          "type": "string",
          "description": "Comma-separated trigger signals (e.g., 'valid,ready')"
        }
      },
      "required": ["file", "state_signal"]
    }
  },
  {
    "name": "vcd_causality_analysis",
    "description": "Find potential root causes for a signal change. Analyzes temporal correlations and identifies causal chains.",
    "parameters": {
      "type": "object",
      "properties": {
        "file": {"type": "string"},
        "effect_signal": {"type": "string", "description": "Signal that changed (e.g., 'error_flag')"},
        "effect_time": {"type": "string", "description": "Time of change (e.g., '17.5us')"},
        "search_window": {"type": "string", "description": "Search window duration (e.g., '100ns')"}
      },
      "required": ["file", "effect_signal", "effect_time"]
    }
  },
  {
    "name": "vcd_anomaly_detect",
    "description": "Automatically detect waveform anomalies: stuck signals, glitches, metastability, bus contention.",
    "parameters": {
      "type": "object",
      "properties": {
        "file": {"type": "string"},
        "signal_filter": {"type": "string", "description": "Signal pattern to analyze"}
      },
      "required": ["file"]
    }
  }
]
```

**Python 调用示例** (`vcd_integrations/openai/example.py`):
```python
import openai
import subprocess
import json

# 加载 function definitions
with open('functions.json') as f:
    functions = json.load(f)

# 调用 GPT-4 with functions
response = openai.ChatCompletion.create(
    model="gpt-4",
    messages=[
        {"role": "user", "content": "分析 sim.vcd 中的 AXI 传输，找出为什么在 17.5us 失败了"}
    ],
    functions=functions,
    function_call="auto"
)

# 处理 function call
if response.choices[0].message.get("function_call"):
    func_name = response.choices[0].message["function_call"]["name"]
    func_args = json.loads(response.choices[0].message["function_call"]["arguments"])
    
    # 执行 VCD Analyzer
    cmd = ["python", "vcd_analyzer.py", func_name.replace("vcd_", ""), "--json"]
    # ... 添加参数
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    # 将结果返回给 GPT-4
    second_response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[
            {"role": "user", "content": "分析 sim.vcd..."},
            response.choices[0].message,
            {"role": "function", "name": func_name, "content": result.stdout}
        ]
    )
    
    print(second_response.choices[0].message["content"])
```


---

### 3.3 LangChain Tool 封装

为 LangChain Agent 提供工具封装：

**文件**：`vcd_integrations/langchain/tools.py`

```python
from langchain.tools import BaseTool
from typing import Optional, Type
from pydantic import BaseModel, Field
import subprocess
import json

class ProtocolDecodeInput(BaseModel):
    file: str = Field(description="Path to VCD file")
    protocol: str = Field(description="Protocol type: axi4, apb, uart, spi")
    signals: Optional[str] = Field(default="*", description="Signal pattern")

class VCDProtocolDecodeTool(BaseTool):
    name = "vcd_protocol_decode"
    description = """
    Decode bus protocol transactions from VCD waveform.
    Useful for analyzing AXI/APB/UART/SPI communication.
    Returns transactions, violations, and performance statistics.
    """
    args_schema: Type[BaseModel] = ProtocolDecodeInput
    
    def _run(self, file: str, protocol: str, signals: str = "*") -> str:
        cmd = [
            "python", "vcd_analyzer.py", "protocol-decode",
            file, "--protocol", protocol, "--signals", signals, "--json"
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        return result.stdout
    
    async def _arun(self, *args, **kwargs):
        raise NotImplementedError("Async not supported")

class FSMTraceInput(BaseModel):
    file: str = Field(description="Path to VCD file")
    state_signal: str = Field(description="State signal name")

class VCDFSMTraceTool(BaseTool):
    name = "vcd_fsm_trace"
    description = """
    Extract state machine transitions from VCD waveform.
    Detects stuck states and unexpected transitions.
    """
    args_schema: Type[BaseModel] = FSMTraceInput
    
    def _run(self, file: str, state_signal: str) -> str:
        cmd = [
            "python", "vcd_analyzer.py", "fsm-trace",
            file, "--state", state_signal, "--json"
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        return result.stdout

class CausalityInput(BaseModel):
    file: str = Field(description="Path to VCD file")
    effect_signal: str = Field(description="Signal that changed")
    effect_time: str = Field(description="Time of change (e.g., '17.5us')")

class VCDCausalityTool(BaseTool):
    name = "vcd_causality_analysis"
    description = """
    Find potential root causes for a signal change.
    Analyzes temporal correlations within a time window.
    """
    args_schema: Type[BaseModel] = CausalityInput
    
    def _run(self, file: str, effect_signal: str, effect_time: str) -> str:
        cmd = [
            "python", "vcd_analyzer.py", "causality",
            file, "--effect", effect_signal, "--at", effect_time, "--json"
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        return result.stdout

class AnomalyDetectInput(BaseModel):
    file: str = Field(description="Path to VCD file")
    signal_filter: Optional[str] = Field(default="*", description="Signal pattern")

class VCDAnomalyDetectTool(BaseTool):
    name = "vcd_anomaly_detect"
    description = """
    Automatically detect waveform anomalies.
    Finds stuck signals, glitches, metastability, bus contention.
    """
    args_schema: Type[BaseModel] = AnomalyDetectInput
    
    def _run(self, file: str, signal_filter: str = "*") -> str:
        cmd = [
            "python", "vcd_analyzer.py", "anomaly-detect",
            file, "--filter", signal_filter, "--json"
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        return result.stdout
```

**使用示例** (`vcd_integrations/langchain/example.py`):
```python
from langchain.agents import initialize_agent, AgentType
from langchain.chat_models import ChatOpenAI
from tools import (
    VCDProtocolDecodeTool,
    VCDFSMTraceTool,
    VCDCausalityTool,
    VCDAnomalyDetectTool
)

# 初始化工具
tools = [
    VCDProtocolDecodeTool(),
    VCDFSMTraceTool(),
    VCDCausalityTool(),
    VCDAnomalyDetectTool()
]

# 创建 Agent
llm = ChatOpenAI(model="gpt-4", temperature=0)
agent = initialize_agent(
    tools,
    llm,
    agent=AgentType.OPENAI_FUNCTIONS,
    verbose=True
)

# 使用
response = agent.run(
    "分析 sim.vcd 中的 AXI 传输，找出为什么在 17.5us 出现错误"
)
print(response)
```


---

### 3.4 通用 REST API（可选）

为不支持上述协议的 Agent 提供 HTTP 接口：

**文件**：`vcd_integrations/rest_api/server.py`

```python
from flask import Flask, request, jsonify
import subprocess
import json

app = Flask(__name__)

@app.route('/api/v1/skills', methods=['GET'])
def list_skills():
    """返回所有可用的 skills"""
    with open('vcd_skill_manifest.json') as f:
        manifest = json.load(f)
    return jsonify(manifest)

@app.route('/api/v1/protocol-decode', methods=['POST'])
def protocol_decode():
    data = request.json
    cmd = [
        "python", "vcd_analyzer.py", "protocol-decode",
        data["file"], "--protocol", data["protocol"],
        "--signals", data.get("signals", "*"), "--json"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return jsonify(json.loads(result.stdout))

@app.route('/api/v1/fsm-trace', methods=['POST'])
def fsm_trace():
    data = request.json
    cmd = [
        "python", "vcd_analyzer.py", "fsm-trace",
        data["file"], "--state", data["state_signal"], "--json"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return jsonify(json.loads(result.stdout))

@app.route('/api/v1/causality', methods=['POST'])
def causality():
    data = request.json
    cmd = [
        "python", "vcd_analyzer.py", "causality",
        data["file"], "--effect", data["effect_signal"],
        "--at", data["effect_time"], "--json"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return jsonify(json.loads(result.stdout))

@app.route('/api/v1/anomaly-detect', methods=['POST'])
def anomaly_detect():
    data = request.json
    cmd = [
        "python", "vcd_analyzer.py", "anomaly-detect",
        data["file"], "--filter", data.get("signal_filter", "*"), "--json"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return jsonify(json.loads(result.stdout))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
```

**使用示例**：
```bash
# 启动服务
python vcd_integrations/rest_api/server.py

# 调用
curl -X POST http://localhost:5000/api/v1/protocol-decode \
  -H "Content-Type: application/json" \
  -d '{"file": "sim.vcd", "protocol": "axi4", "signals": "m_axi_*"}'
```

---

## Phase 4: 文档与示例（2 周）

### 4.1 文档结构

```
docs/
├── README.md                      # 总览
├── skill_reference.md             # 每个 Skill 的详细说明
├── integration_guide.md           # 如何接入不同 Agent
├── json_schema.md                 # JSON 输出格式规范
├── error_handling.md              # 错误码和处理指南
└── examples/
    ├── claude_code_integration.md # Claude Code 集成示例
    ├── gpt4_integration.md        # GPT-4 集成示例
    ├── langchain_integration.md   # LangChain 集成示例
    └── use_cases/
        ├── debug_axi_timeout.md   # 案例：调试 AXI 超时
        ├── find_fsm_deadlock.md   # 案例：查找状态机死锁
        └── analyze_cdc_issue.md   # 案例：分析跨时钟域问题
```

### 4.2 Skill Reference 示例

**docs/skill_reference.md**：

```markdown
# VCD Analyzer Skill Reference

## protocol-decode

### 描述
解码总线协议传输（AXI/APB/UART/SPI），识别握手时序、检测协议违例、计算性能指标。

### 命令格式
```bash
python vcd_analyzer.py protocol-decode <file> --protocol <type> --signals <pattern> [options] --json
```

### 参数

| 参数 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `file` | string | 是 | VCD 文件路径 |
| `--protocol` | enum | 是 | 协议类型：axi4, apb, uart, spi |
| `--signals` | string | 否 | 信号匹配模式（默认：*） |
| `--begin` | time | 否 | 开始时间（如：10ns） |
| `--end` | time | 否 | 结束时间（如：1us） |
| `--json` | flag | 是 | 输出 JSON 格式 |

### 输出字段

#### transactions (array)
每个事务包含：
- `id`: 事务编号
- `type`: 类型（read/write）
- `start_time`: 开始时间
- `end_time`: 结束时间
- `addr`: 地址
- `data`: 数据数组
- `status`: 状态（OKAY/SLVERR/DECERR）

#### violations (array)
协议违例：
- `type`: 违例类型
- `time`: 发生时间
- `severity`: 严重程度（error/warning）
- `description`: 详细描述

#### statistics (object)
性能统计：
- `total_transactions`: 总事务数
- `read_count`: 读事务数
- `write_count`: 写事务数
- `avg_latency`: 平均延迟
- `bandwidth_utilization`: 带宽利用率

### 使用场景

1. **验证 AXI 传输正确性**
   ```bash
   python vcd_analyzer.py protocol-decode sim.vcd --protocol axi4 --signals m_axi_* --json
   ```

2. **分析性能瓶颈**
   - 检查 `bandwidth_utilization` 是否过低
   - 查看 `avg_latency` 是否符合预期

3. **调试协议违例**
   - 查看 `violations` 数组
   - 根据 `suggestions` 进一步分析

### Agent 使用建议

当用户询问：
- "AXI 传输成功了吗？" → 检查 `transactions[].status`
- "为什么带宽这么低？" → 查看 `statistics.bandwidth_utilization` 和 `suggestions`
- "有协议错误吗？" → 检查 `violations` 数组

### 示例输出

见 Phase 1.1 的完整示例。
```


---

### 4.3 集成指南示例

**docs/examples/claude_code_integration.md**：

```markdown
# Claude Code 集成指南

## 方式一：MCP Server（推荐）

### 1. 安装 MCP Server

```bash
# 克隆仓库
git clone https://github.com/yourusername/vcd_analyzer.git
cd vcd_analyzer

# 安装依赖（仅 MCP Server 需要）
pip install mcp
```

### 2. 配置 Claude Desktop

编辑 `~/Library/Application Support/Claude/claude_desktop_config.json`（macOS）或
`%APPDATA%\Claude\claude_desktop_config.json`（Windows）：

```json
{
  "mcpServers": {
    "vcd-analyzer": {
      "command": "python",
      "args": ["/path/to/vcd_analyzer/vcd_integrations/mcp/server.py"]
    }
  }
}
```

### 3. 重启 Claude Desktop

重启后，Claude Code 会自动加载 VCD Analyzer 工具。

### 4. 使用示例

```
User: 分析 sim.vcd 中的 AXI 传输

Claude: [自动调用 vcd_protocol_decode]
        我分析了 sim.vcd，发现：
        - 共 15 个 AXI 传输
        - 1 个协议违例：在 18.2us 时 WVALID 在 AWVALID 之前拉高
        - 带宽利用率 65%，略低
        
        需要我详细分析违例原因吗？

User: 是的

Claude: [自动调用 vcd_causality_analysis]
        违例的根本原因是...
```

## 方式二：直接命令行调用

如果不想配置 MCP Server，可以让 Claude Code 直接调用命令：

```
User: 帮我分析 sim.vcd 中的 AXI 传输

Claude: 我会使用 VCD Analyzer 来分析：
        [执行] python vcd_analyzer.py protocol-decode sim.vcd --protocol axi4 --json
        
        结果显示...
```

## 最佳实践

1. **文件路径**：使用绝对路径或相对于项目根目录的路径
2. **大文件**：对于 >100MB 的 VCD，先用 `--begin/--end` 限制时间范围
3. **信号过滤**：使用 `--signals` 精确匹配，避免分析无关信号
4. **多步分析**：
   - 先用 `anomaly-detect` 快速扫描
   - 再用 `protocol-decode` 深入分析
   - 最后用 `causality` 定位根因
```

---

### 4.4 使用案例示例

**docs/examples/use_cases/debug_axi_timeout.md**：

```markdown
# 案例：调试 AXI 传输超时

## 问题描述

仿真日志显示 AXI 写传输在 17.5us 超时，需要找出原因。

## 分析步骤

### Step 1: 快速扫描异常

```bash
python vcd_analyzer.py anomaly-detect sim.vcd --filter "*axi*" --json
```

**结果**：
```json
{
  "anomalies": [
    {
      "type": "stuck_signal",
      "signal": "m_axi_bvalid",
      "time_range": ["17.2us", "18.0us"],
      "severity": "warning",
      "description": "Signal stuck at 0 for 800ns"
    }
  ],
  "suggestions": [
    "m_axi_bvalid stuck, check slave response logic"
  ]
}
```

**发现**：`bvalid` 信号长时间为 0，说明 slave 没有响应。

### Step 2: 解码 AXI 协议

```bash
python vcd_analyzer.py protocol-decode sim.vcd --protocol axi4 --signals m_axi_* --begin 17us --end 18us --json
```

**结果**：
```json
{
  "transactions": [
    {
      "id": 5,
      "type": "write",
      "start_time": "17.1us",
      "end_time": null,
      "addr": "0x2000",
      "status": "pending"
    }
  ],
  "violations": [
    {
      "type": "timeout",
      "time": "17.5us",
      "description": "Write response not received within 400ns"
    }
  ]
}
```

**发现**：写地址 0x2000，但一直没收到响应。

### Step 3: 因果分析

```bash
python vcd_analyzer.py causality sim.vcd --effect timeout_flag --at 17.5us --json
```

**结果**：
```json
{
  "potential_causes": [
    {
      "signal": "slave_fifo_full",
      "change_time": "17.05us",
      "correlation": 0.92,
      "pattern": "slave_fifo_full=1 → timeout"
    }
  ]
}
```

**根因**：Slave 的 FIFO 满了，无法接收新的写请求。

## 解决方案

1. 增大 slave FIFO 深度
2. 或在 master 端添加 backpressure 处理

## Agent 对话示例

```
User: sim.vcd 中 AXI 传输在 17.5us 超时了，帮我找原因

Agent: 我来分析一下...
       [调用 anomaly-detect]
       发现 m_axi_bvalid 信号卡住了，说明 slave 没响应。
       
       [调用 protocol-decode]
       写地址是 0x2000，一直等不到响应。
       
       [调用 causality]
       根本原因是 slave_fifo_full 在 17.05us 拉高了。
       
       建议：增大 slave FIFO 深度或添加流控机制。
```
```


---

## 实现路线图

### 时间线总览

| 阶段 | 时间 | 交付物 |
|------|------|--------|
| Phase 1 | 1-2 月 | 4 个核心 Skill 实现 |
| Phase 2 | 2-3 周 | 标准化接口和 manifest |
| Phase 3 | 1 月 | 多 Agent 适配器 |
| Phase 4 | 2 周 | 文档和示例 |

### Phase 1 详细计划（1-2 月）

#### Week 1-2: protocol-decode (AXI4)
- [x] AXI4 信号识别和角色分配
- [x] 握手时序重建
- [x] 读/写事务解析
- [x] 协议违例检测（burst length mismatch、incomplete transactions）
- [x] 性能统计（带宽、延迟）
- [x] 单元测试
- [x] 文档编写（docs/protocol_decode.md）

**完成日期**: 2026-05-25

**实现亮点**:
- 支持单次和突发（burst）读写事务
- 自动识别 AXI4 五通道信号（AW/W/B/AR/R）
- 事件驱动的握手检测，正确处理多 beat 传输
- JSON 和文本双输出格式
- 包含建议（suggestions）引导后续分析

**测试覆盖**:
- 基本读写事务
- 突发传输（2 beat）
- 协议违例检测
- 性能统计计算

#### Week 3-4: protocol-decode (APB/UART/SPI)
- [x] APB 协议解码（APB3 with PSLVERR）
- [x] UART 协议解码（自动 baud rate 检测）
- [x] SPI 协议解码（Mode 0）
- [x] 统一输出格式
- [x] 集成测试

**完成日期**: 2026-05-26

**实现亮点**:
- **APB**: 状态机解码（IDLE/SETUP/ACCESS），支持 SLVERR 检测，批量时间戳处理避免误判
- **UART**: 自动 baud rate 检测（基于最小转换间隔），ASCII 字符识别，TX/RX 双通道
- **SPI**: Mode 0（CPOL=0/CPHA=0），CS_N 分隔事务，MOSI/MISO 同时解码
- 协议特定的输出格式和 suggestions

**测试覆盖**:
- APB: 基本读写、统计、错误识别
- UART: 字节解码、波特率检测
- SPI: 基本事务、CS 分隔、8 位传输
- 通用：不支持协议错误处理、JSON 格式合规

#### Week 5-6: fsm-trace
- [x] 状态信号识别
- [x] 状态转换提取
- [x] 异常检测（stuck state）
- [x] 状态停留时间统计
- [x] JSON 和文本双输出格式
- [x] 单元测试
- [x] 文档编写（docs/fsm_trace.md）

**完成日期**: 2026-05-25

**实现亮点**:
- 支持任意状态编码（二进制、one-hot、Gray码等）
- 自动检测 stuck state 异常（可配置阈值）
- 详细的状态统计（出现次数、平均/最小/最大停留时间）
- 按总时间排序的状态列表，快速识别关键状态
- 包含建议（suggestions）引导后续分析

**测试覆盖**:
- 基本状态转换提取
- Stuck state 检测
- 时间范围过滤
- 状态统计计算
- JSON 输出格式验证

#### Week 7-8: causality + anomaly-detect
- [x] 时间窗口信号变化收集
- [x] 时间相关性计算
- [x] 因果链路识别
- [x] Clock signal 自动过滤
- [x] 历史模式匹配（historical correlation）
- [x] 置信度评估（high/medium/low）
- [x] 单元测试
- [x] 文档编写（docs/causality.md）
- [x] 异常模式库（stuck、glitch、metastability、bus_contention）
- [x] 自动阈值检测（基于分析窗口）

**causality 完成日期**: 2026-05-26
**anomaly-detect 完成日期**: 2026-05-26

**causality 实现亮点**:
- 双因子相关性评分：时间接近度（40%）+ 历史模式匹配（60%）
- 自动识别并过滤时钟信号（基于命名 + 频率启发式）
- 因果链路按时间顺序展示，方便理解事件序列
- 三级置信度评估（high/medium/low）
- 包含可操作的 suggestions 引导后续分析

**anomaly-detect 实现亮点**:
- 4 种异常类型：stuck_signal、glitch、metastability、bus_contention
- Stuck 严重度分级（warning/error/critical 基于持续时间）
- Glitch 检测（单 bit 信号窄脉冲）
- Metastability vs Bus contention 智能区分
- 可配置阈值（stuck-threshold、glitch-threshold）

**测试覆盖**:
- causality: 基本分析、顶部原因、时钟过滤、因果链、窄窗口、时间评分、JSON
- anomaly-detect: 4 种异常检测、信号过滤、时间过滤、严重度统计、按时间排序

### Phase 2 详细计划（2-3 周）

#### Week 1: 统一 JSON 格式
- [x] 定义标准响应结构（envelope: status / skill / execution_time_ms / input / result / metadata / suggestions）
- [x] 重构所有 Skill 输出（cmd_protocol_decode / cmd_fsm_trace / cmd_causality / cmd_anomaly_detect）
- [x] 添加 `suggestions` 字段生成逻辑（每个 Skill 都有 Agent-facing 提示）
- [x] 错误处理标准化（SkillError + run_skill() 包装器，9 类错误码）

#### Week 2: Manifest 系统
- [x] 创建 `vcd_skill_manifest.json`（4 个 Skill 完整 schema + 错误码列表）
- [x] 实现 `--skill-manifest` 命令
- [x] 实现 `--skill-info <name>` 命令
- [x] JSON Schema 验证（capabilities 字段中包含 input_schema / result_schema）

#### Week 3: 测试和优化
- [x] 端到端测试（verify/test_skill_envelope.py，12 个测试）
- [x] CI 集成（新增 envelope 测试步骤 + manifest 命令验证）
- [x] 错误处理完善（4 类常见错误的结构化路径全覆盖）
- [x] 文档（docs/skill_envelope.md）

**完成日期**: 2026-05-27

**实现亮点**:
- **新增统一框架**：`SkillError` 异常 + `run_skill()` 包装器 + `_skill_envelope()` / `_skill_error_envelope()` 构建器
- **9 类标准错误码**：`FILE_NOT_FOUND` / `PARSE_ERROR` / `INVALID_PROTOCOL` / `SIGNAL_NOT_FOUND` / `INVALID_TIME_RANGE` / `INVALID_ARGUMENT` / `INSUFFICIENT_DATA` / `RESOURCE_LIMIT` / `INTERNAL_ERROR`
- **向后兼容**：所有原有的 `result.transactions` / `input.protocol` 等字段保持不变，新增字段（`metadata` / `execution_time_ms`）层叠在外
- **manifest 自描述**：Agent 无需任何先验知识就能通过 `--skill-manifest` 发现工具能力
- **示例输入回显**：`input` 字段重述每次调用的参数，方便日志审计和重放

**Phase 2 完成标准**:
- [x] 所有 Skill 输出统一 JSON 格式
- [x] `--skill-manifest` 命令可用
- [x] 错误处理完善（结构化 error 对象 + 退出码 != 0）

**测试覆盖**:
- 4 个 Skill envelope shape 验证
- 4 类错误码路径验证（INVALID_PROTOCOL / SIGNAL_NOT_FOUND × 2 / INVALID_TIME_RANGE）
- 4 个 `--skill-info` 查询验证
- manifest 错误码与实现一致性验证
- 总计 12 个新测试，全部通过

### Phase 3 详细计划（1 月）

#### Week 1: MCP Server
- [ ] 实现 MCP Server
- [ ] 工具注册和调用
- [ ] 配置文件示例
- [ ] 本地测试

#### Week 2: OpenAI Functions
- [ ] 编写 functions.json
- [ ] Python 调用示例
- [ ] 测试 GPT-4 集成

#### Week 3: LangChain Tools
- [ ] 实现 Tool 类
- [ ] Pydantic Schema 定义
- [ ] Agent 集成示例

#### Week 4: REST API（可选）
- [ ] Flask 服务实现
- [ ] API 文档
- [ ] Docker 部署配置

### Phase 4 详细计划（2 周）

#### Week 1: 核心文档
- [ ] Skill Reference 完整文档
- [ ] JSON Schema 规范
- [ ] 错误处理指南
- [ ] Integration Guide

#### Week 2: 示例和案例
- [ ] Claude Code 集成示例
- [ ] GPT-4 集成示例
- [ ] LangChain 集成示例
- [ ] 3 个真实调试案例

---

## 技术实现细节

### 协议解码器架构

```python
# vcd_analyzer.py 中新增

class ProtocolDecoder:
    """协议解码器基类"""
    def __init__(self, vcd, signal_map):
        self.vcd = vcd
        self.signals = signal_map
    
    def decode(self, t0, t1):
        """返回 transactions, violations, statistics"""
        raise NotImplementedError

class AXI4Decoder(ProtocolDecoder):
    def __init__(self, vcd, signal_pattern):
        # 自动识别信号角色
        signals = self._identify_signals(signal_pattern)
        super().__init__(vcd, signals)
    
    def _identify_signals(self, pattern):
        """识别 AWVALID, AWREADY, AWADDR 等"""
        matched = self.vcd.match(pattern)
        signal_map = {}
        for sid in matched:
            path = self.vcd.signals[sid]['path']
            if 'awvalid' in path.lower():
                signal_map['awvalid'] = sid
            elif 'awready' in path.lower():
                signal_map['awready'] = sid
            # ... 其他信号
        return signal_map
    
    def decode(self, t0, t1):
        transactions = []
        violations = []
        
        # 重建握手时序
        for t, sid, val in self.vcd.iter_events(t0, t1, self.signals.values()):
            # 状态机跟踪
            # ...
        
        # 计算统计
        statistics = self._compute_stats(transactions)
        
        return transactions, violations, statistics
```

### 状态机提取算法

```python
class FSMExtractor:
    def __init__(self, vcd, state_signal):
        self.vcd = vcd
        self.state_sid = vcd.match(state_signal)[0]
    
    def extract(self, t0, t1):
        transitions = []
        current_state = None
        state_start_time = None
        
        for t, sid, val in self.vcd.iter_events(t0, t1, [self.state_sid]):
            if current_state is not None:
                transitions.append({
                    'from': current_state,
                    'to': val,
                    'time': t,
                    'duration_in_from': t - state_start_time
                })
            current_state = val
            state_start_time = t
        
        # 检测异常
        anomalies = self._detect_anomalies(transitions)
        
        return transitions, anomalies
    
    def _detect_anomalies(self, transitions):
        anomalies = []
        
        # Stuck state: 停留时间 > 阈值
        for trans in transitions:
            if trans['duration_in_from'] > STUCK_THRESHOLD:
                anomalies.append({
                    'type': 'stuck_state',
                    'state': trans['from'],
                    'duration': trans['duration_in_from']
                })
        
        return anomalies
```

### 因果分析算法

```python
class CausalityAnalyzer:
    def __init__(self, vcd):
        self.vcd = vcd
    
    def analyze(self, effect_signal, effect_time, window):
        t0 = effect_time - window
        t1 = effect_time
        
        # 收集窗口内所有信号变化
        changes = []
        for t, sid, val in self.vcd.iter_events(t0, t1):
            changes.append({
                'signal': self.vcd.signals[sid]['path'],
                'time': t,
                'value': val,
                'delta': effect_time - t
            })
        
        # 计算相关性
        causes = []
        for change in changes:
            correlation = self._compute_correlation(
                change['signal'], effect_signal
            )
            if correlation > 0.7:
                causes.append({
                    **change,
                    'correlation': correlation
                })
        
        # 按相关性排序
        causes.sort(key=lambda x: x['correlation'], reverse=True)
        
        return causes
    
    def _compute_correlation(self, signal_a, signal_b):
        """基于历史数据计算时间相关性"""
        # 简化版：统计 A 变化后 B 变化的频率
        # 实际可以用更复杂的算法
        pass
```


---

## 项目结构

```
vcd_analyzer/
├── vcd_analyzer.py                 # 核心解析引擎（保持单文件）
├── vcd_skill_manifest.json         # Skill 能力描述
├── vcd_integrations/               # Agent 适配器（可选安装）
│   ├── mcp/
│   │   ├── server.py              # MCP Server
│   │   ├── config.json            # Claude Desktop 配置示例
│   │   └── README.md
│   ├── openai/
│   │   ├── functions.json         # OpenAI Function Calling
│   │   ├── example.py
│   │   └── README.md
│   ├── langchain/
│   │   ├── tools.py               # LangChain Tools
│   │   ├── example.py
│   │   └── README.md
│   └── rest_api/
│       ├── server.py              # REST API
│       ├── requirements.txt
│       └── README.md
├── docs/
│   ├── README.md
│   ├── skill_reference.md         # Skill 详细文档
│   ├── integration_guide.md       # 集成指南
│   ├── json_schema.md             # JSON 格式规范
│   ├── error_handling.md          # 错误处理
│   └── examples/
│       ├── claude_code_integration.md
│       ├── gpt4_integration.md
│       ├── langchain_integration.md
│       └── use_cases/
│           ├── debug_axi_timeout.md
│           ├── find_fsm_deadlock.md
│           └── analyze_cdc_issue.md
├── verify/                         # 测试套件（保持现有）
│   ├── test_*.py
│   └── fixtures/
├── version_notes/                  # 版本历史（保持现有）
└── README.md                       # 更新说明新功能
```

---

## 兼容性保证

### 向后兼容

所有现有命令保持不变：
- `info`, `list`, `dump`, `summary`, `snapshot`, `compare`, `search`
- 现有的 `--json` 输出格式保持兼容
- 现有的测试套件全部通过

### 新功能作为扩展

新的 Skill 作为独立子命令：
- `protocol-decode`
- `fsm-trace`
- `causality`
- `anomaly-detect`

用户可以选择：
1. **仅使用核心功能**：只需 `vcd_analyzer.py`，零依赖
2. **使用 Skill 功能**：需要实现对应的解码器
3. **集成到 Agent**：安装对应的适配器（如 MCP Server）

---

## 性能考虑

### 大文件处理

1. **流式解析**：保持现有的流式架构，不一次性加载全部数据
2. **时间窗口**：所有 Skill 都支持 `--begin/--end` 限制范围
3. **信号过滤**：使用 `--signals` 减少处理的信号数量
4. **增量分析**：协议解码器使用状态机，O(n) 时间复杂度

### 内存优化

1. **按需加载**：只解析需要的信号
2. **结果限制**：默认限制输出条目数（可配置）
3. **垃圾回收**：及时释放中间数据结构

---

## 测试策略

### 单元测试

每个 Skill 独立测试：
```python
# verify/test_protocol_decode.py
def test_axi4_basic_write():
    result = run_skill('protocol-decode', 'axi_write.vcd', 
                       protocol='axi4', signals='m_axi_*')
    assert result['status'] == 'success'
    assert len(result['result']['transactions']) == 1
    assert result['result']['transactions'][0]['type'] == 'write'

def test_axi4_protocol_violation():
    result = run_skill('protocol-decode', 'axi_violation.vcd',
                       protocol='axi4')
    assert len(result['result']['violations']) > 0
```

### 集成测试

测试 Agent 适配器：
```python
# verify/test_mcp_integration.py
def test_mcp_server_list_tools():
    tools = mcp_client.list_tools()
    assert 'vcd_protocol_decode' in [t.name for t in tools]

def test_mcp_server_call_tool():
    result = mcp_client.call_tool('vcd_protocol_decode', {
        'file': 'sim.vcd',
        'protocol': 'axi4'
    })
    assert 'transactions' in result
```

### 端到端测试

真实场景测试：
```python
# verify/test_e2e_debug_flow.py
def test_debug_axi_timeout():
    # 1. 异常检测
    anomalies = run_skill('anomaly-detect', 'timeout.vcd')
    assert any(a['type'] == 'stuck_signal' for a in anomalies['result']['anomalies'])
    
    # 2. 协议解码
    protocol = run_skill('protocol-decode', 'timeout.vcd', protocol='axi4')
    assert any(v['type'] == 'timeout' for v in protocol['result']['violations'])
    
    # 3. 因果分析
    causality = run_skill('causality', 'timeout.vcd',
                         effect_signal='timeout_flag', effect_time='17.5us')
    assert len(causality['result']['potential_causes']) > 0
```

---

## 风险和缓解

### 风险 1：协议解码复杂度高

**缓解**：
- 先实现 AXI4-Lite（简化版），再扩展到 AXI4-Full
- 提供配置文件让用户自定义信号映射
- 开源社区贡献协议解码器

### 风险 2：因果分析准确性

**缓解**：
- 明确标注为"潜在原因"而非"确定原因"
- 提供 `correlation` 和 `confidence` 字段
- 允许用户调整时间窗口和阈值

### 风险 3：Agent 集成维护成本

**缓解**：
- 核心功能与适配器解耦
- 适配器作为独立模块，可选安装
- 提供通用 REST API 作为兜底方案

### 风险 4：性能问题

**缓解**：
- 保持流式解析架构
- 提供时间窗口和信号过滤
- 性能测试和基准

---

## 成功指标

### Phase 1 完成标准
- [x] 4 个 Skill 全部实现（protocol-decode AXI4/APB/UART/SPI、fsm-trace、causality、anomaly-detect）
- [x] 单元测试覆盖（33 个 skill 测试 + 36 个核心测试 = 69 个测试全部通过）
- [x] 能正确解码真实 VCD 文件（使用 iverilog 生成的真实波形验证）
- [x] CI 集成（GitHub Actions multi-OS multi-Python 矩阵测试）

### Phase 2 完成标准
- [x] 所有 Skill 输出统一 JSON 格式
- [x] `--skill-manifest` 命令可用
- [x] 错误处理完善

### Phase 3 完成标准
- [ ] 至少 2 个 Agent 适配器可用（MCP + OpenAI）
- [ ] 集成测试通过
- [ ] 能在真实 Agent 中调用

### Phase 4 完成标准
- [ ] 完整文档发布
- [ ] 至少 3 个使用案例
- [ ] 集成指南清晰易懂

### 最终验收标准
- [ ] 一个 AI Agent 能通过 Skill 接口完成完整的调试流程
- [ ] 社区反馈积极
- [ ] 性能满足实际使用需求

---

## 下一步行动

### 立即开始（本周）
1. 创建 `vcd_skill_manifest.json` 框架
2. 实现 AXI4 信号识别逻辑
3. 搭建协议解码器基础架构

### 短期目标（1 个月）
1. 完成 AXI4 协议解码器
2. 实现统一 JSON 输出格式
3. 编写第一个集成示例

### 中期目标（3 个月）
1. 完成所有 4 个 Skill
2. 实现 MCP Server
3. 发布 Beta 版本

### 长期目标（6 个月）
1. 社区贡献更多协议解码器
2. 优化性能和用户体验
3. 发布 2.0 正式版

---

## 总结

本计划将 VCD Analyzer 从"波形查询工具"升级为"AI Agent 可调用的智能分析 Skill"：

**核心价值**：
- 为 AI Agent 提供"理解波形"的能力
- 标准化接口，易于集成
- 保持核心简洁，按需扩展

**设计原则**：
- 我们做 Skill，不做 Agent
- JSON First，Agent 友好
- 向后兼容，渐进增强

**预期效果**：
- AI Agent 能自动完成复杂的波形调试任务
- 降低 RTL 验证的门槛
- 提升调试效率 10x+

---

**文档版本**：v1.0  
**创建日期**：2026-05-25  
**作者**：VCD Analyzer Team
