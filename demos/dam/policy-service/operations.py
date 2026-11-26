"""The operations of an MCP tool call, from the shape of its arguments only.

Some tools do several things through their arguments: GitHub's ``issue_write`` creates an issue,
or, with ``method: update``, closes it, reopens it, reassigns it or edits it, in one call. A
description ("it never reassigns issues") and the company policy ("only testers close an issue")
speak of those operations, so each call is decided per operation, named ``<tool>.<operation>``,
the names aiac decides from (aiac ``demo/dam/github-mcp-tools.json``). A call that does several
at once yields all of them, and each must be allowed. A shape nothing here knows is the operation
``<tool>.unknown``, which no description grants.

Only which arguments are present, and the values of ``method`` and ``state``, are read: never
what an issue says.
"""

from __future__ import annotations

from typing import Any

ISSUE_EDIT_FIELDS = ("title", "body", "labels", "milestone", "type", "duplicate_of")


def operations_of(tool: str, arguments: Any) -> list[str]:
    """``issue_write`` as its operations; every other tool as itself."""
    if tool != "issue_write":
        return [tool]
    args = arguments if isinstance(arguments, dict) else {}
    method = args.get("method")
    if method == "create":
        return ["issue_write.create"]
    if method != "update":
        return ["issue_write.unknown"]
    ops: list[str] = []
    state = args.get("state")
    if state == "closed":
        ops.append("issue_write.close")
    elif state == "open":
        ops.append("issue_write.reopen")
    elif state is not None:
        ops.append("issue_write.unknown")
    if "assignees" in args:
        ops.append("issue_write.assign")
    if any(field in args for field in ISSUE_EDIT_FIELDS):
        ops.append("issue_write.edit")
    return ops or ["issue_write.edit"]
