from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP

# Resolve DB path relative to project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "support.db"

# Create MCP server
mcp = FastMCP(
    name="customer-support-db",
    host="127.0.0.1",  # used for SSE transport
    port=8001,  # used for SSE transport
    json_response=True,
)


def _get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {k: row[k] for k in row.keys()}


@mcp.tool()
def get_customer(customer_id: int) -> Optional[Dict[str, Any]]:
    """
    Get a single customer by id from the customers table.

    Args:
        customer_id: Primary key in customers.id

    Returns:
        A dictionary with customer fields, or null if not found.
    """
    with _get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM customers WHERE id = ?",
            (customer_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return _row_to_dict(row)


@mcp.tool()
def list_customers(status: str = "active", limit: int = 50) -> List[Dict[str, Any]]:
    """
    List customers filtered by status.

    Args:
        status: 'active' or 'disabled'
        limit: max number of rows to return

    Returns:
        List of customer records as dictionaries.
    """
    if status not in {"active", "disabled"}:
        raise ValueError("status must be 'active' or 'disabled'")

    with _get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT * FROM customers
            WHERE status = ?
            ORDER BY id
            LIMIT ?
            """,
            (status, limit),
        )
        rows = cur.fetchall()
        return [_row_to_dict(r) for r in rows]


@mcp.tool()
def update_customer(customer_id: int, data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Update fields on a customer.

    Args:
        customer_id: customers.id
        data: fields to update - any of name, email, phone, status

    Returns:
        Updated customer record.

    Raises:
        ValueError if customer does not exist or invalid fields are given.
    """
    allowed_fields = {"name", "email", "phone", "status"}
    if not data:
        raise ValueError("No fields provided to update")

    unknown = set(data.keys()) - allowed_fields
    if unknown:
        raise ValueError(f"Unknown fields in data: {sorted(unknown)}")

    if "status" in data and data["status"] not in {"active", "disabled"}:
        raise ValueError("status must be 'active' or 'disabled'")

    with _get_connection() as conn:
        cur = conn.cursor()

        # Check exists
        cur.execute("SELECT id FROM customers WHERE id = ?", (customer_id,))
        if cur.fetchone() is None:
            raise ValueError(f"Customer {customer_id} does not exist")

        # Build dynamic update query
        columns = []
        values: List[Any] = []
        for field, value in data.items():
            columns.append(f"{field} = ?")
            values.append(value)
        values.append(customer_id)

        sql = f"UPDATE customers SET {', '.join(columns)} WHERE id = ?"
        cur.execute(sql, values)
        conn.commit()

        # Return updated row
        cur.execute("SELECT * FROM customers WHERE id = ?", (customer_id,))
        row = cur.fetchone()
        if row is None:
            raise RuntimeError("Customer vanished after update")
        return _row_to_dict(row)


@mcp.tool()
def create_ticket(
    customer_id: int,
    issue: str,
    priority: str = "medium",
) -> Dict[str, Any]:
    """
    Create a new ticket for a customer.

    Args:
        customer_id: FK to customers.id
        issue: text description of the issue
        priority: 'low', 'medium', or 'high'

    Returns:
        Created ticket record.
    """
    if priority not in {"low", "medium", "high"}:
        raise ValueError("priority must be one of 'low', 'medium', 'high'")

    with _get_connection() as conn:
        cur = conn.cursor()

        # Ensure customer exists
        cur.execute("SELECT id FROM customers WHERE id = ?", (customer_id,))
        if cur.fetchone() is None:
            raise ValueError(f"Customer {customer_id} does not exist")

        cur.execute(
            """
            INSERT INTO tickets (customer_id, issue, status, priority)
            VALUES (?, ?, 'open', ?)
            """,
            (customer_id, issue, priority),
        )
        conn.commit()
        ticket_id = cur.lastrowid

        cur.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,))
        row = cur.fetchone()
        if row is None:
            raise RuntimeError("Ticket insert failed")
        return _row_to_dict(row)


@mcp.tool()
def get_customer_history(customer_id: int) -> List[Dict[str, Any]]:
    """
    Get all tickets for a customer, ordered by created_at descending.

    Args:
        customer_id: tickets.customer_id

    Returns:
        List of ticket records.
    """
    with _get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT * FROM tickets
            WHERE customer_id = ?
            ORDER BY datetime(created_at) DESC, id DESC
            """,
            (customer_id,),
        )
        rows = cur.fetchall()
        return [_row_to_dict(r) for r in rows]


if __name__ == "__main__":
    # SSE transport on localhost:8001
    # This is what MCP Inspector can connect to.
    mcp.run(transport="sse")
