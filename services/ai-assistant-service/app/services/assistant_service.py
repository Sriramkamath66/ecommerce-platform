from __future__ import annotations

import json
import logging
from typing import Any, AsyncGenerator

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a helpful AI shopping assistant for an e-commerce platform.
You help customers find products, compare options, check order status, and answer questions.
You have access to tools to search the product catalog, check inventory, and look up orders.
Be concise, friendly, and helpful. Always cite product names when recommending specific items."""

TOOLS = [
    {
        "name": "search_products",
        "description": "Search the product catalog by query",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query for products"},
                "limit": {
                    "type": "integer",
                    "default": 5,
                    "description": "Number of results",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_order_status",
        "description": "Get the status of a customer's order by order ID",
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "string",
                    "description": "The order ID to look up",
                }
            },
            "required": ["order_id"],
        },
    },
    {
        "name": "check_inventory",
        "description": "Check if a product is in stock",
        "input_schema": {
            "type": "object",
            "properties": {
                "product_id": {
                    "type": "string",
                    "description": "The product ID to check",
                }
            },
            "required": ["product_id"],
        },
    },
]


class AssistantService:
    """Drive the Claude agentic loop with RAG context and tool execution."""

    def __init__(
        self,
        anthropic_client: Any,
        rag_service: Any,
        http_client: Any,
        settings: Any,
    ) -> None:
        self.anthropic_client = anthropic_client
        self.rag_service = rag_service
        self.http_client = http_client
        self.settings = settings

    async def process_message(
        self,
        session_id: str,
        user_id: str,
        message: str,
        history: list[dict],
    ) -> AsyncGenerator[str, None]:
        """Run the agentic loop and yield text chunks as they arrive from Claude.

        The generator yields raw text delta strings suitable for SSE streaming.
        """
        return self._process_message_impl(session_id, user_id, message, history)

    async def _process_message_impl(
        self,
        session_id: str,
        user_id: str,
        message: str,
        history: list[dict],
    ) -> AsyncGenerator[str, None]:
        # 1. Retrieve relevant products via RAG
        rag_results = await self.rag_service.retrieve_context(message)
        context_text = self.rag_service.format_context(rag_results)

        # 2. Inject RAG context into the user message
        user_content = (
            f"[Product catalog context]\n{context_text}\n\n"
            f"[Customer message]\n{message}"
        )

        # 3. Build the messages array: history + this turn's user message
        messages: list[dict] = list(history) + [
            {"role": "user", "content": user_content}
        ]

        # 4. Agentic loop
        max_iterations = 5
        iteration = 0
        while iteration < max_iterations:
            iteration += 1
            tool_use_blocks: list[Any] = []
            assistant_content: list[Any] = []
            final_message: Any = None

            async with self.anthropic_client.messages.stream(
                model=self.settings.LLM_MODEL,
                max_tokens=2048,
                system=SYSTEM_PROMPT,
                messages=messages,
                tools=TOOLS,
            ) as stream:
                async for text in stream.text_stream:
                    yield text

                final_message = await stream.get_final_message()
                assistant_content = final_message.content

            if final_message.stop_reason == "end_turn":
                break

            elif final_message.stop_reason == "tool_use":
                # Append assistant turn (full content blocks, serialised)
                messages.append(
                    {
                        "role": "assistant",
                        "content": [
                            block.model_dump() for block in assistant_content
                        ],
                    }
                )

                # Execute every tool_use block and collect results
                tool_results: list[dict] = []
                for block in assistant_content:
                    if block.type == "tool_use":
                        result = await self.execute_tool(
                            block.name, block.input, user_id
                        )
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": result,
                            }
                        )

                messages.append({"role": "user", "content": tool_results})

            else:
                # Unexpected stop reason — exit loop gracefully
                logger.warning(
                    "Unexpected stop_reason=%r in session %s",
                    final_message.stop_reason,
                    session_id,
                )
                break

    async def execute_tool(
        self, tool_name: str, tool_input: dict, user_id: str
    ) -> str:
        """Dispatch a Claude tool call and return a string result."""
        if tool_name == "search_products":
            query = tool_input.get("query", "")
            limit = int(tool_input.get("limit", 5))
            results = await self.rag_service.retrieve_context(query, limit)
            formatted = self.rag_service.format_context(results)
            return formatted if formatted else "No products found."

        elif tool_name == "get_order_status":
            order_id = tool_input.get("order_id", "")
            url = f"{self.settings.ORDER_SERVICE_URL}/orders/{order_id}"
            try:
                response = await self.http_client.get(url, timeout=10.0)
                if response.status_code == 404:
                    return json.dumps({"error": f"Order {order_id} not found."})
                response.raise_for_status()
                return response.text
            except Exception as exc:  # noqa: BLE001
                logger.error("get_order_status failed for %s: %s", order_id, exc)
                return json.dumps(
                    {"error": f"Could not retrieve order {order_id}: {exc}"}
                )

        elif tool_name == "check_inventory":
            product_id = tool_input.get("product_id", "")
            url = f"{self.settings.INVENTORY_SERVICE_URL}/inventory/{product_id}"
            try:
                response = await self.http_client.get(url, timeout=10.0)
                if response.status_code == 404:
                    return json.dumps(
                        {"error": f"Product {product_id} not found in inventory."}
                    )
                response.raise_for_status()
                return response.text
            except Exception as exc:  # noqa: BLE001
                logger.error("check_inventory failed for %s: %s", product_id, exc)
                return json.dumps(
                    {"error": f"Could not check inventory for {product_id}: {exc}"}
                )

        else:
            return "Tool not found"
