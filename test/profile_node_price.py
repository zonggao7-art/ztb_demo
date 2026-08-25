"""对重构后的 price_inquiry 节点进行一次端到端 profile。"""

import logging
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)

from langchain_core.messages import HumanMessage
from agent.nodes.price_inquiry import node_price_inquiry  # type: ignore

if __name__ == "__main__":
    state = {"messages": [HumanMessage(content="查询皮艺沙发的中标记录")]}
    result = node_price_inquiry(state)
    print("=" * 80)
    print(result["business_result"]["answer"])
