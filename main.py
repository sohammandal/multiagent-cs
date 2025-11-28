from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
import time
from typing import Any, Dict, List

import httpx
import nest_asyncio
import uvicorn

# --- A2A client patch from lab notebook ---
from a2a.client import client as real_client_module
from a2a.client.card_resolver import A2ACardResolver
from dotenv import load_dotenv


class PatchedClientModule:
    def __init__(self, real_module) -> None:
        for attr in dir(real_module):
            if not attr.startswith("_"):
                setattr(self, attr, getattr(real_module, attr))
        # expose A2ACardResolver to match older imports
        self.A2ACardResolver = A2ACardResolver


sys.modules["a2a.client.client"] = PatchedClientModule(real_client_module)  # type: ignore

# --- A2A imports ---

from a2a.client import ClientConfig, ClientFactory, create_text_message_object
from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
    TransportProtocol,
)
from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH

# --- ADK imports ---
from google.adk.a2a.executor.a2a_agent_executor import (
    A2aAgentExecutor,
    A2aAgentExecutorConfig,
)
from google.adk.agents import Agent, SequentialAgent
from google.adk.agents.remote_a2a_agent import RemoteA2aAgent
from google.adk.artifacts import InMemoryArtifactService
from google.adk.memory.in_memory_memory_service import InMemoryMemoryService
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools.mcp_tool.mcp_session_manager import SseServerParams
from google.adk.tools.mcp_tool.mcp_toolset import MCPToolset

# --- Env setup ---

load_dotenv()

os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "FALSE")
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "multiagent-cs-hw")
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "us-central1")

if "GOOGLE_API_KEY" not in os.environ:
    raise RuntimeError("GOOGLE_API_KEY not set. Put it in .env as GOOGLE_API_KEY=...")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
)
logger = logging.getLogger("multiagent-cs")

# MCP server URL for SSE transport
MCP_DB_URL = "http://127.0.0.1:8001/sse"

MODEL_NAME = "gemini-2.0-flash-lite"

# --- Build ADK agents ---


def build_customer_data_agent() -> Agent:
    """
    Customer Data Agent:
    - Uses MCP tools to talk to support.db
    - No guessing of database state
    """
    db_toolset = MCPToolset(
        connection_params=SseServerParams(
            url=MCP_DB_URL,
            timeout=300.0,
            sse_read_timeout=300.0,
        )
    )

    instruction = """
    You are the Customer Data Agent for a customer support system.

    - You are the only agent allowed to access the customer database.
    - Always use MCP tools instead of hallucinating data.
    - Tools available include:
      - get_customer(customer_id)
      - list_customers(status, limit)
      - update_customer(customer_id, data)
      - create_ticket(customer_id, issue, priority)
      - get_customer_history(customer_id)

    Responsibilities:
    - Given a customer id, return a concise JSON summary of the customer.
    - For queries about ticket history, call get_customer_history and summarize.
    - For updates (email, phone, status), call update_customer and return the new state.
    - For new issues, call create_ticket and include the ticket id and priority.

    Always respond in JSON with this shape:

    {
      "source_agent": "customer_data_agent",
      "customer_id": <int or null>,
      "db_calls": [
        {"tool": "...", "args": {...}, "summary": "..."}
      ],
      "customer": { ... customer row or null ... },
      "tickets": [ ... ticket rows if fetched ... ],
      "notes": "short explanation for other agents"
    }
    """

    return Agent(
        model=MODEL_NAME,
        name="customer_data_agent",
        instruction=instruction,
        tools=[db_toolset],
    )


def build_support_agent() -> Agent:
    """
    Support Agent:
    - Handles customer support language and escalation logic
    - Does not talk to the database directly
    """
    instruction = """
    You are the Support Agent in a multi-agent customer service system.

    Inputs:
    - The original user query.
    - A JSON object called 'customer_context' with fields:
      - customer: customer record or null
      - tickets: list of ticket records (possibly empty)
      - db_calls: trace of what the data agent did
      - notes: free form notes from the data agent

    Responsibilities:
    - Interpret the user query and the supplied customer context.
    - Handle common customer support flows such as:
      - Account help (upgrades, profile updates).
      - Billing issues (double charge, refund requests).
      - Status of tickets and high priority issues.
    - Decide whether to escalate:
      - Escalate when the user expresses strong frustration or urgency,
        or when automatic resolution is not appropriate.
      - For escalation, clearly label escalation_reason.

    Always respond in JSON with this shape:

    {
      "source_agent": "support_agent",
      "final_reply": "text you would send to the customer",
      "actions": [
        "human_followup: ...",
        "note_on_account: ...",
        "created_ticket:<id>"  // optional
      ],
      "escalate": true or false,
      "escalation_reason": "short explanation or empty string",
      "coordination_log": [
        "Support agent received context ...",
        "Decided to escalate / not escalate because ..."
      ]
    }

    Keep the final_reply polite, concise, and clearly structured.
    """

    return Agent(
        model=MODEL_NAME,
        name="support_agent",
        instruction=instruction,
    )


def build_router_host_agent(
    remote_customer_data: RemoteA2aAgent,
    remote_support: RemoteA2aAgent,
) -> SequentialAgent:
    """
    Router Host Agent:
    - Acts as orchestrator that uses the two remote agents.
    - Implemented as a SequentialAgent that first calls the data agent,
      then the support agent with the context.
    """
    # SequentialAgent uses the sub_agents in sequence.
    # The internal prompt will handle passing intermediate results.
    host = SequentialAgent(
        name="router_host_agent",
        sub_agents=[remote_customer_data, remote_support],
    )
    return host


# --- A2A server helpers ---


def create_agent_a2a_server(
    agent: Agent, agent_card: AgentCard
) -> A2AStarletteApplication:
    """
    Create an A2A server for any ADK agent.

    This matches the pattern from the lab notebook.
    """
    runner = Runner(
        app_name=agent.name,
        agent=agent,
        artifact_service=InMemoryArtifactService(),
        session_service=InMemorySessionService(),
        memory_service=InMemoryMemoryService(),
    )

    config = A2aAgentExecutorConfig()
    executor = A2aAgentExecutor(runner=runner, config=config)

    request_handler = DefaultRequestHandler(
        agent_executor=executor,
        task_store=InMemoryTaskStore(),
    )

    return A2AStarletteApplication(
        agent_card=agent_card,
        http_handler=request_handler,
    )


async def run_agent_server(agent: Any, agent_card: AgentCard, port: int) -> None:
    app = create_agent_a2a_server(agent, agent_card)

    config = uvicorn.Config(
        app.build(),
        host="127.0.0.1",
        port=port,
        log_level="warning",
        loop="none",
    )
    server = uvicorn.Server(config)
    await server.serve()


# --- Build agents and AgentCards ---


# Actual ADK agents
customer_data_agent = build_customer_data_agent()
support_agent = build_support_agent()

# AgentCards and A2A URLs
CUSTOMER_DATA_URL = "http://127.0.0.1:10020"
SUPPORT_URL = "http://127.0.0.1:10021"
ROUTER_URL = "http://127.0.0.1:10022"

customer_data_agent_card = AgentCard(
    name="Customer Data Agent",
    url=CUSTOMER_DATA_URL,
    description="Accesses customer and ticket data via MCP tools",
    version="1.0",
    capabilities=AgentCapabilities(streaming=True),
    default_input_modes=["text/plain"],
    default_output_modes=["application/json"],
    preferred_transport=TransportProtocol.jsonrpc,
    skills=[
        AgentSkill(
            id="get_and_update_customer",
            name="Get and update customer records",
            description="Uses MCP tools to fetch, update, and summarize customers and tickets",
            tags=["customer", "tickets", "mcp"],
            examples=[
                "Get customer information for ID 5",
                "Show ticket history for customer 3",
                "Update customer 10 email to new@example.com",
            ],
        )
    ],
)

support_agent_card = AgentCard(
    name="Support Agent",
    url=SUPPORT_URL,
    description="Handles support conversations and escalation decisions using provided customer context",
    version="1.0",
    capabilities=AgentCapabilities(streaming=True),
    default_input_modes=["text/plain"],
    default_output_modes=["application/json"],
    preferred_transport=TransportProtocol.jsonrpc,
    skills=[
        AgentSkill(
            id="handle_support_issue",
            name="Handle support issues",
            description="Resolves customer issues, decides on escalation, and drafts responses",
            tags=["support", "billing", "escalation"],
            examples=[
                "I need help upgrading my account",
                "I was charged twice, please refund",
            ],
        )
    ],
)

# Remote A2A wrappers for router
remote_customer_data_agent = RemoteA2aAgent(
    name="customer_data_remote",
    description="Remote A2A Customer Data Agent that talks to MCP DB",
    agent_card=f"{CUSTOMER_DATA_URL}{AGENT_CARD_WELL_KNOWN_PATH}",
)

remote_support_agent = RemoteA2aAgent(
    name="support_remote",
    description="Remote A2A Support Agent that talks to customers",
    agent_card=f"{SUPPORT_URL}{AGENT_CARD_WELL_KNOWN_PATH}",
)

# Router host agent and card
router_host_agent = build_router_host_agent(
    remote_customer_data=remote_customer_data_agent,
    remote_support=remote_support_agent,
)

router_agent_card = AgentCard(
    name="Router Host Agent",
    url=ROUTER_URL,
    description="Router that orchestrates Customer Data and Support agents using A2A",
    version="1.0",
    capabilities=AgentCapabilities(streaming=True),
    default_input_modes=["text/plain"],
    default_output_modes=["application/json"],
    preferred_transport=TransportProtocol.jsonrpc,
    skills=[
        AgentSkill(
            id="customer_service_router",
            name="Customer Service Router",
            description=(
                "Receives user queries, calls the Customer Data Agent to fetch context "
                "from MCP, then calls the Support Agent to craft the final response. "
                "Logs the coordination steps."
            ),
            tags=["routing", "orchestration", "multi-agent"],
            examples=[
                "I'm customer 12345 and need help upgrading my account",
                "I've been charged twice, please refund immediately!",
                "Update my email and show my ticket history",
            ],
        )
    ],
)


# --- Start all A2A servers in background ---


nest_asyncio.apply()


async def start_all_servers() -> None:
    tasks = [
        asyncio.create_task(
            run_agent_server(customer_data_agent, customer_data_agent_card, 10020)
        ),
        asyncio.create_task(run_agent_server(support_agent, support_agent_card, 10021)),
        asyncio.create_task(
            run_agent_server(router_host_agent, router_agent_card, 10022)
        ),
    ]

    await asyncio.sleep(2.0)

    logger.info("A2A servers started:")
    logger.info("  Customer Data Agent: %s", CUSTOMER_DATA_URL)
    logger.info("  Support Agent:       %s", SUPPORT_URL)
    logger.info("  Router Host Agent:   %s", ROUTER_URL)

    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        logger.info("Server tasks cancelled")


def run_servers_in_background() -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(start_all_servers())


# --- Simple A2A client to talk to router host ---


class A2ASimpleClient:
    """Utility to call A2A servers, same pattern as the lab notebook."""

    def __init__(self, default_timeout: float = 240.0):
        self._agent_info_cache: Dict[str, Dict[str, Any] | None] = {}
        self.default_timeout = default_timeout

    async def create_task(self, agent_url: str, message: str) -> str:
        timeout_config = httpx.Timeout(
            timeout=self.default_timeout,
            connect=10.0,
            read=self.default_timeout,
            write=10.0,
            pool=5.0,
        )

        async with httpx.AsyncClient(timeout=timeout_config) as httpx_client:
            if (
                agent_url in self._agent_info_cache
                and self._agent_info_cache[agent_url]
            ):
                agent_card_data = self._agent_info_cache[agent_url]
            else:
                agent_card_response = await httpx_client.get(
                    f"{agent_url}{AGENT_CARD_WELL_KNOWN_PATH}"
                )
                agent_card_data = self._agent_info_cache[agent_url] = (
                    agent_card_response.json()
                )  # type: ignore[assignment]

            agent_card = AgentCard(**agent_card_data)  # type: ignore[arg-type]

            config = ClientConfig(
                httpx_client=httpx_client,
                supported_transports=[
                    TransportProtocol.jsonrpc,
                    TransportProtocol.http_json,
                ],
                use_client_preference=True,
            )

            factory = ClientFactory(config)
            client = factory.create(agent_card)

            message_obj = create_text_message_object(content=message)

            responses: List[Any] = []
            async for response in client.send_message(message_obj):
                responses.append(response)

            if responses and isinstance(responses[0], tuple) and len(responses[0]) > 0:
                task = responses[0][0]
                try:
                    return task.artifacts[0].parts[0].root.text  # type: ignore[index]
                except (AttributeError, IndexError):
                    return str(task)

            return "No response received"


# --- Test scenarios from assignment ---


async def run_test_scenarios() -> None:
    client = A2ASimpleClient()

    print("\n====================")
    print("Scenario 1: Simple Query")
    print('Query: "Get customer information for ID 5"')
    print("====================")
    resp1 = await client.create_task(
        ROUTER_URL,
        "Get customer information for ID 5.",
    )
    print(resp1)

    print("\n====================")
    print("Scenario 2: Coordinated Query")
    print('Query: "I\'m customer 12345 and need help upgrading my account"')
    print("====================")
    resp2 = await client.create_task(
        ROUTER_URL,
        "I'm customer 5 and need help upgrading my account to premium tier.",
    )
    print(resp2)

    print("\n====================")
    print("Scenario 3: Complex Query")
    print('Query: "Show me all active customers who have open tickets"')
    print("====================")
    resp3 = await client.create_task(
        ROUTER_URL,
        "Show me all active customers who have open tickets. "
        "Summarize them grouped by customer, with ticket priorities.",
    )
    print(resp3)

    print("\n====================")
    print("Scenario 4: Escalation")
    print('Query: "I\'ve been charged twice, please refund immediately!"')
    print("====================")
    resp4 = await client.create_task(
        ROUTER_URL,
        "I'm customer 1. I've been charged twice, please refund immediately! I am very upset.",
    )
    print(resp4)

    print("\n====================")
    print("Scenario 5: Multi-Intent")
    print('Query: "Update my email to new@email.com and show my ticket history"')
    print("====================")
    resp5 = await client.create_task(
        ROUTER_URL,
        "I'm customer 2. Update my email to new@email.com "
        "and then show my ticket history.",
    )
    print(resp5)


def main() -> None:
    # Start A2A servers (router + 2 specialists) in background
    server_thread = threading.Thread(
        target=run_servers_in_background,
        daemon=True,
    )
    server_thread.start()

    # Give them time to boot
    time.sleep(3.0)

    # Run all test scenarios against the router host agent
    asyncio.run(run_test_scenarios())

    print("\nAll scenarios executed. Check above output and logs for A2A coordination.")


if __name__ == "__main__":
    main()
