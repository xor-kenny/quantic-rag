"""MCP server. Every agent data/policy operation is exposed as a typed MCP tool."""
from mcp.server.fastmcp import FastMCP

from . import data, rag

mcp = FastMCP(
    "ClearHR Operations",
    instructions="Synthetic HR-policy tools. Never perform irreversible actions.",
    log_level="WARNING",
)

_ready_index: dict | None = None


@mcp.tool()
def search_policy_documents(query: str, limit: int = 4) -> list[dict]:
    """Retrieve grounded policy chunks with citation metadata."""
    return rag.search(query, min(max(limit, 1), 8))


@mcp.tool()
def get_retrieval_status() -> dict:
    """Report the effective non-secret RAG settings in this MCP child process.

    This is an operational diagnostic, not an agent capability. The FastAPI
    health endpoint calls it through the same stdio session as every real tool
    call, so it reports the child process's environment rather than the web
    parent's configuration. The LLM capability allow-list intentionally omits
    it, preventing diagnostic calls from consuming an agent turn.
    """
    # `run_stdio()` stores the warmed index before accepting the MCP handshake.
    # The fallback keeps direct unit use safe without making normal health
    # probes rebuild an index or reload a dense model.
    index = _ready_index if _ready_index is not None else rag.ensure_ready()
    return rag.runtime_status(index)


@mcp.tool()
def get_policy_section(document: str, section: str) -> list[dict]:
    """Get a policy section by document filename/title and section name.

    If the requested heading is not present, return a structured recovery hint
    with the available headings for that document.  Keeping the response a
    list preserves the MCP schema and lets the agent correct a near-miss
    heading without treating it as policy evidence.
    """
    # The persisted index includes an internal embedding used only by retrieval.
    # Do not send it over MCP: it inflates model context and is not citation data.
    chunks = rag.load_index()["chunks"]
    matches = [
        {key: value for key, value in item.items() if key != "embedding"}
        for item in chunks
        if item["document"] == document and item["section"].lower() == section.lower()
    ]
    if matches:
        return matches[:8]

    available_sections = sorted({
        item["section"] for item in chunks if item["document"] == document
    })
    return [{
        "error": "section_not_found",
        "document": document,
        "requested_section": section,
        "available_sections": available_sections,
    }]


@mcp.tool()
def lookup_employee_profile(employee_id: str) -> dict:
    """Look up a synthetic employee profile."""
    return data.employee(employee_id) or {"error": "Employee not found", "employee_id": employee_id}


@mcp.tool()
def check_pto_balance(employee_id: str) -> dict:
    """Return a synthetic PTO balance."""
    return data.pto_balance(employee_id) or {"error": "PTO record not found", "employee_id": employee_id}


@mcp.tool()
def lookup_benefits_status(employee_id: str) -> dict:
    """Return synthetic benefits enrollment information."""
    return data.benefits(employee_id) or {"error": "Benefits record not found", "employee_id": employee_id}


@mcp.tool()
def create_mock_hr_ticket(
    employee_id: str, summary: str, category: str, confirmed: bool = False
) -> dict:
    """Create a confirmed mock draft only; it never files a real ticket."""
    # Enforce this at the tool boundary as well as in the agent. That protects
    # callers using a future agent implementation or direct MCP client.
    if not confirmed:
        return {
            "error": "confirmation_required",
            "detail": "Set confirmed=true only after the user explicitly confirms the mock draft.",
        }
    if not data.employee(employee_id):
        return {"error": "Employee not found", "employee_id": employee_id}
    return data.create_ticket(employee_id, summary, category)


def run_stdio() -> None:
    """Warm selected RAG resources, then serve the real stdio MCP transport."""
    # The MCP child, rather than the FastAPI parent, owns the optional dense
    # encoder.  It finishes index/model readiness before the stdio handshake,
    # so /health cannot report a usable tool service while the model is still
    # downloading or warming.
    global _ready_index
    _ready_index = rag.ensure_ready()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    run_stdio()
