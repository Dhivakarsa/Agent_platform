"""
Shared rendering for a user's executed tasks.

Both the Usage screen and the Logs screen expand a user into the same underlying rows:
one per executed task, carrying that task's token cost and its individual tool calls.
This module renders those rows so the two screens stay consistent.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

_STATUS_ICON = {
    "completed": "🟢",
    "success": "🟢",
    "waiting_approval": "🟡",
    "rejected": "🔴",
    "failed": "🔴",
    "error": "🔴",
}


def _icon(status: str) -> str:
    return _STATUS_ICON.get(str(status).lower(), "🔵")


def render_task_token_table(activity: list[dict[str, Any]], key_prefix: str) -> None:
    """
    One row per executed task with the tokens that task consumed.

    This is the Usage view: what was asked, whether it succeeded, and what it cost.
    """
    if not activity:
        st.caption("No tasks executed yet.")
        return

    rows = []
    for item in activity:
        usage = item.get("token_usage") or {}
        rows.append(
            {
                "": _icon(item.get("status", "")),
                "Task": (item.get("task") or "")[:90],
                "Status": str(item.get("status", "")).replace("_", " ").title(),
                "Tools": len(item.get("tools_used") or []),
                "Prompt": usage.get("input_tokens", 0),
                "Output": usage.get("output_tokens", 0),
                "Total Tokens": usage.get("total_tokens", 0),
                "Latency (ms)": round(item.get("duration_ms") or 0.0),
                "When": str(item.get("created_at", ""))[:19],
            }
        )

    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)

    total = sum(r["Total Tokens"] for r in rows)
    st.caption(
        f"{len(rows)} tasks · {total:,} model tokens total"
        + ("" if total else " · no model configured, so no tokens were consumed")
    )


def render_task_log_tables(activity: list[dict[str, Any]], key_prefix: str) -> None:
    """
    One expander per executed task containing its tool calls as a table.

    This is the Logs view: the task, then each dispatched tool with its status and log.
    """
    if not activity:
        st.caption("No tasks executed yet.")
        return

    for idx, item in enumerate(activity):
        status = item.get("status", "")
        failed = item.get("failed_step_count", 0)
        label = (
            f"{_icon(status)} {(item.get('task') or 'Task')[:80]} "
            f"· {item.get('step_count', 0)} tool call(s)"
            + (f" · {failed} failed" if failed else "")
        )

        with st.expander(label):
            meta_a, meta_b, meta_c = st.columns(3)
            with meta_a:
                st.caption(f"Request ID: `{item.get('run_id')}`")
            with meta_b:
                st.caption(f"Status: `{status}` · Approval: `{item.get('approval_status')}`")
            with meta_c:
                usage = item.get("token_usage") or {}
                st.caption(f"Tokens: `{usage.get('total_tokens', 0):,}`")

            steps = item.get("steps") or []
            if not steps:
                st.caption("No tool was dispatched for this task.")
                continue

            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "": _icon(s.get("status", "")),
                            "Tool": s.get("tool_name"),
                            "Description": (
                                s.get("error_message")
                                or f"Dispatched with {len(s.get('input_args') or {})} argument(s)"
                            )[:80],
                            "Status": str(s.get("status", "")).replace("_", " ").title(),
                            "Latency (ms)": round(s.get("latency_ms") or 0.0),
                            "When": str(s.get("created_at", ""))[:19],
                        }
                        for s in steps
                    ]
                ),
                use_container_width=True,
                hide_index=True,
            )

            step_labels = [f"{i + 1}. {s.get('tool_name')}" for i, s in enumerate(steps)]
            chosen = st.selectbox(
                "Inspect a tool call",
                step_labels,
                key=f"{key_prefix}_step_{idx}",
            )
            step = steps[step_labels.index(chosen)]

            detail_in, detail_out = st.columns(2)
            with detail_in:
                st.markdown("**Input arguments**")
                st.json(step.get("input_args") or {})
            with detail_out:
                st.markdown("**Response payload**")
                st.json(step.get("response_payload") or {})
            if step.get("error_message"):
                st.error(step["error_message"])
