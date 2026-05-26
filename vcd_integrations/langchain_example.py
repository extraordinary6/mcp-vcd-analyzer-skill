"""LangChain Agent example using VCD Analyzer tools.

Requires:
    pip install langchain langchain-openai openai

Run:
    OPENAI_API_KEY=sk-... python vcd_integrations/langchain_example.py path/to/sim.vcd
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vcd_integrations.langchain_tools import build_tools


def main():
    if len(sys.argv) < 2:
        sys.exit("Usage: langchain_example.py <path/to/sim.vcd>")
    vcd_path = sys.argv[1]

    try:
        from langchain_openai import ChatOpenAI
        from langchain.agents import AgentExecutor, create_tool_calling_agent
        from langchain_core.prompts import ChatPromptTemplate
    except ImportError:
        sys.exit("Install dependencies: pip install langchain langchain-openai")

    tools = build_tools()

    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)

    prompt = ChatPromptTemplate.from_messages([
        ("system",
         "You are a digital-design debug assistant. Use the vcd_* tools "
         "to inspect VCD waveforms. Always start with vcd_anomaly_detect "
         "to triage, then drill down with vcd_causality, vcd_protocol_decode, "
         "or vcd_fsm_trace as needed."),
        ("human", "{input}"),
        ("placeholder", "{agent_scratchpad}"),
    ])

    agent = create_tool_calling_agent(llm, tools, prompt)
    executor = AgentExecutor(agent=agent, tools=tools, verbose=True,
                              max_iterations=6)

    response = executor.invoke({
        "input": "Please analyze {!r} and report any issues.".format(vcd_path)
    })
    print("\n=== Final Output ===")
    print(response["output"])


if __name__ == '__main__':
    main()
