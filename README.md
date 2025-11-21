# MultiAgent Customer Service System

> Multi agent customer support orchestration using A2A coordination and MCP tools.

## Overview

This project implements a **multi agent customer service automation system** with:

* A Router agent that analyzes user intent and coordinates tasks
* Specialist agents for customer data and support workflows
* A full MCP server providing structured tools to access and update customer records
* Multi step A2A communication for task allocation, negotiation, and escalation
* A SQLite database initialized with sample users and tickets via `database_setup.py`

The system is designed to demonstrate real world agent collaboration patterns, suitable for practical automation scenarios.

## Project Structure

```
multiagent-cs/
├── main.py                   # Entry point for running test scenarios
├── support.db                # Local SQLite database (auto created)
├── src/
│   ├── agents/               # Router, Support, CustomerData agents
│   ├── mcp/                  # MCP server, tool schemas, handlers
│   └── database_setup.py     # Creates DB, tables, triggers, sample data
├── pyproject.toml            # Project config
├── uv.lock                   # Locked dependencies
├── .pre-commit-config.yaml   # Pre commit hooks
└── README.md
```

## Local Development

### 1. Clone the repo

```bash
git clone git@github.com:sohammandal/multiagent-cs.git
cd multiagent-cs
```

### 2. Install uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 3. Sync environment and install dependencies

```bash
uv sync
source .venv/bin/activate
```

### 4. Set up pre commit

```bash
pre-commit install

# If missing, install via uv
uv tool install pre-commit
```

### 5. (Optional) Run checks on all files

```bash
pre-commit run --all-files
```

### 6. Initialize the database

You can run this directly. It will create `support.db` in the project root.

```bash
python src/database_setup.py
```

Choose yes when prompted to insert sample data.

### 7. Run the multi agent system

```bash
python main.py
```

## Test Scenarios

This system supports the required homework flows:

* **Simple query**: direct MCP data retrieval
* **Task allocation**: router calls data agent then support agent
* **Negotiation**: agents exchange context before resolving
* **Multi step**: router decomposes tasks across agents
* **Escalation**: urgent cases routed to support with extra context

These scenarios can be executed from `main.py`.
