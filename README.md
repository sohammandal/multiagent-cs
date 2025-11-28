# Multi Agent Customer Service System

> Multi agent customer support orchestration using A2A coordination and MCP tools.

## Overview

This project implements a multi agent customer service automation system with:

- A Router Host Agent that analyzes user intent and coordinates tasks
- A Customer Data Agent that is the only agent allowed to touch the database, via MCP tools
- A Support Agent that handles customer facing responses, escalation, and actions
- A full MCP server that exposes a SQLite customer support database as structured tools
- Multi step A2A communication for task allocation, negotiation, and escalation
- A SQLite database initialized with sample users and tickets via `database_setup.py`

The system is designed to mirror realistic customer support workflows and to demonstrate
A2A coordination plus MCP integration for the homework assignment.

## Conclusion

Working on this project made the A2A model feel much more concrete. Wiring up the Router Host Agent, a dedicated Customer Data Agent and a Support Agent showed how cleanly responsibilities can be separated when agents talk to each other instead of each one owning its own random tools. 

The MCP server was an especially nice fit here, because it forced the Customer Data Agent to treat the database as a well defined external capability with typed tools rather than letting the model hallucinate SQL or fabricate customer objects. Having the Router treat the two specialists as remote A2A agents also mirrored what a real multi service production system might look like.

There were a few real challenges. Getting the A2A SDK, the ADK and the MCP server to agree on ports, URLs and timeouts took some iteration, and Gemini rate limits and API key settings caused failures until the configuration was cleaned up. Debugging multi hop interactions was harder than debugging a single agent, so detailed logging of A2A events and saving full terminal output in `runs/main_output.txt` and `runs/mcp_output.txt` turned out to be essential. 

Overall, the project was a good exercise in designing agent boundaries, thinking carefully about data ownership and making the system observable enough that multi agent coordination bugs can actually be tracked down.

## System Architecture

### Agents

All agents are implemented using the Google ADK in `main.py`:

- **Customer Data Agent**
  - Uses MCP tools to read and write `support.db`
  - Calls:
    - `get_customer(customer_id)`
    - `list_customers(status, limit)`
    - `update_customer(customer_id, data)`
    - `create_ticket(customer_id, issue, priority)`
    - `get_customer_history(customer_id)`
  - Returns structured JSON with `customer`, `tickets`, and a trace of `db_calls`

- **Support Agent**
  - Receives the original user query plus a `customer_context` JSON object
  - Decides how to respond, when to escalate, and which notes or follow up actions to request
  - Always returns structured JSON with:
    - `final_reply` for the customer
    - `actions` list for humans or downstream systems
    - `escalate` and `escalation_reason`
    - `coordination_log` describing internal reasoning

- **Router Host Agent**
  - Exposed as its own A2A agent
  - Uses `RemoteA2aAgent` wrappers for the Customer Data and Support agents
  - For each incoming query:
    - Calls the Customer Data Agent first to fetch or update context via MCP
    - Passes the resulting context into the Support Agent
    - Returns the Support Agent's JSON as the final result

### A2A Coordination

Each agent exposes an Agent Card at `/.well-known/agent-card.json` and runs its own
A2A server on localhost:

- Customer Data Agent: `http://127.0.0.1:10020`
- Support Agent: `http://127.0.0.1:10021`
- Router Host Agent: `http://127.0.0.1:10022`

`main.py` includes a small `A2ASimpleClient` that:

1. Fetches the Router's Agent Card
2. Sends user queries to the Router over JSON RPC
3. Prints the final JSON reply returned from the Support Agent

### MCP Server and Database

The MCP server in `src/mcp/db_mcp_server.py` exposes the database as tools using `FastMCP`.
It connects to `support.db` in the project root.

Tools:

- `get_customer(customer_id)`  
- `list_customers(status="active", limit=50)`  
- `update_customer(customer_id, data)`  
- `create_ticket(customer_id, issue, priority="medium")`  
- `get_customer_history(customer_id)`

The database schema matches the assignment:

**customers**

- `id` INTEGER PRIMARY KEY
- `name` TEXT NOT NULL
- `email` TEXT
- `phone` TEXT
- `status` TEXT (`'active'` or `'disabled'`)
- `created_at` TIMESTAMP
- `updated_at` TIMESTAMP

**tickets**

- `id` INTEGER PRIMARY KEY
- `customer_id` INTEGER (FK to `customers.id`)
- `issue` TEXT NOT NULL
- `status` TEXT (`'open'`, `'in_progress'`, `'resolved'`)
- `priority` TEXT (`'low'`, `'medium'`, `'high'`)
- `created_at` DATETIME

`src/database_setup.py` is a helper script that creates the tables, triggers, inserts
sample data, and can run sample queries.

## Project Structure


```bash
multiagent-cs/
├── README.md
├── LICENSE
├── main.py                  # Entry point that starts all A2A agents and runs scenarios
├── pyproject.toml           # uv / PEP 621 project configuration
├── requirements.txt         # Package list for non uv installs
├── uv.lock                  # Lockfile managed by uv
├── .env.example             # Example env file with GOOGLE_API_KEY
├── .pre-commit-config.yaml  # Pre-commit hooks
├── .python-version
├── runs/
│   ├── main_output.txt      # Saved terminal output from running main.py
│   └── mcp_output.txt       # Saved terminal output from running the MCP server
└── src/
    ├── database_setup.py    # Creates and populates support.db
    └── mcp/
        └── db_mcp_server.py # FastMCP server exposing DB tools
```

`support.db` is created in the project root when you run `src/database_setup.py`.

## Setup and Installation

You can use `uv` (recommended) or a plain `venv` with `requirements.txt`.

### 1. Clone the repo

```bash
git clone git@github.com:sohammandal/multiagent-cs.git
cd multiagent-cs
```

### 2. Install dependencies with `uv`

```bash
# Install uv if needed
curl -LsSf https://astral.sh/uv/install.sh | sh

# Create the virtual environment and install dependencies
uv sync

# Activate the virtual environment
source .venv/bin/activate
```

If you prefer a plain `venv`:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Environment variables

Copy `.env.example` to `.env` and set your Gemini key:

```bash
cp .env.example .env
# then edit .env and put:
# GOOGLE_API_KEY=your_real_key
```

The code uses `GOOGLE_API_KEY` and the Gemini API with the model:

```text
gemini-2.0-flash-lite
```

### 4. Initialize the database

Run the database setup script from the project root. This will create `support.db`
and optionally insert sample data.

```bash
uv run python src/database_setup.py
```

When prompted:

* Answer `y` if you want to insert the sample customers and tickets
* Optionally run the interactive sample queries if you want to inspect the data

### 5. Run the MCP server

In one terminal (with the venv activated):

```bash
uv run python src/mcp/db_mcp_server.py
```

You should see logs showing the server listening on:

```text
http://127.0.0.1:8001
```

and responding to `/sse` and `ListToolsRequest`. Sample output from this step is saved in:

```text
runs/mcp_output.txt
```

### 6. Run the multi agent A2A system

Open a second terminal, activate the same venv, and from the project root run:

```bash
uv run python main.py
```

This will:

1. Start three A2A agents in the background:

   * Customer Data Agent on port 10020
   * Support Agent on port 10021
   * Router Host Agent on port 10022
2. Use `A2ASimpleClient` to send a series of test queries to the Router
3. Print the structured JSON results from the Support Agent

A complete captured run is saved in:

```text
runs/main_output.txt
```

which shows the log output and JSON for each scenario.

## Test Scenarios

`main.py` automatically runs the five scenarios against the Router Host Agent:

1. **Simple Query**

   * Query: `"Get customer information for ID 5"`
   * Flow: Router calls Customer Data Agent via A2A, which calls `get_customer` via MCP.

2. **Coordinated Query (Task allocation)**

   * Query: `"I'm customer 12345 and need help upgrading my account"`
   * Flow: Router asks Customer Data Agent for customer 5, then asks Support Agent to
     handle an upgrade for that customer.

3. **Complex Query (Multi step and negotiation)**

   * Query: `"Show me all active customers who have open tickets"`
   * Flow: Router asks for active customers and ticket information. Support Agent decides
     to escalate when it cannot satisfy the exact report request with the available tools.

4. **Escalation**

   * Query: `"I've been charged twice, please refund immediately!"`
   * Flow: Router fetches context via Customer Data Agent, Support Agent recognizes
     urgency, creates a high priority ticket, and provides a clear escalation log.

5. **Multi intent**

   * Query: `"Update my email to new@email.com and show my ticket history"`
   * Flow: Router uses Customer Data Agent to update the email via `update_customer`
     and fetch ticket history via `get_customer_history`, then passes that context
     to Support for the final reply.

These flows demonstrate:

* A2A based task allocation and routing
* Negotiation and escalation decisions
* Multi step coordination across agents and MCP tools

## End to End Demo

The homework allows either a Colab notebook or a python program that runs end to end.
This repository uses the python program approach:

* `main.py` is the end to end entry point.
* `runs/main_output.txt` contains a complete terminal capture showing:

  * Agent startup and A2A logs
  * MCP calls
  * JSON responses for all five required scenarios

## Development Notes

### Pre commit hooks

Optional, but included:

```bash
uv tool install pre-commit
pre-commit install
pre-commit run --all-files
```

### MCP Inspector

Because the MCP server uses SSE on `127.0.0.1:8001`, it can be inspected with MCP tools
such as MCP Inspector by pointing them at that URL.