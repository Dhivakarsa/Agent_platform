"""
Token usage tracking and activity reporting service.

Records real LLM model token usage metadata returned by the Google Gen AI SDK
and generates user-level and admin-level analytics.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional
from sqlalchemy import desc, func, select

from app.db.models import AgentRun, ExecutionLog, TokenUsage, User
from app.db.session import async_session_factory
from app.schemas.usage import (
    AdminUsageOverallSummary,
    AdminUserSummary,
    ExecutionRecordResponse,
    TokenRecordResponse,
    UserUsageSummary,
)

logger = logging.getLogger(__name__)


class TokenUsageService:
    """Tracks model token consumption and aggregates activity statistics."""

    async def record_token_usage(
        self,
        request_id: str,
        user_id: str,
        input_tokens: int,
        output_tokens: int,
        model: str = "gemini-2.5-flash",
    ) -> TokenUsage:
        """Persist a token usage record from a Gemini response."""
        total = input_tokens + output_tokens
        async with async_session_factory() as s:
            entry = TokenUsage(
                request_id=request_id,
                user_id=user_id,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total,
            )
            s.add(entry)
            await s.commit()
            logger.info("Recorded token usage [user=%s, req=%s, total=%d]", user_id, request_id, total)
            return entry

    async def get_user_usage_summary(self, user_id: str) -> UserUsageSummary:
        """Compile complete personal usage statistics for the authenticated user."""
        async with async_session_factory() as s:
            # 1. User details
            user_stmt = select(User).where(User.user_id == user_id)
            user_res = await s.execute(user_stmt)
            user = user_res.scalar_one_or_none()
            email = user.email if user else "user@examplemail.com"

            # 2. Token counts
            tok_stmt = select(
                func.sum(TokenUsage.input_tokens),
                func.sum(TokenUsage.output_tokens),
                func.sum(TokenUsage.total_tokens),
            ).where(TokenUsage.user_id == user_id)
            tok_res = await s.execute(tok_stmt)
            in_tok, out_tok, tot_tok = tok_res.one()

            # 3. Agent Runs (Tasks)
            run_stmt = select(AgentRun).where(AgentRun.user_id == user_id).order_by(desc(AgentRun.id))
            run_res = await s.execute(run_stmt)
            runs = run_res.scalars().all()

            total_reqs = len(runs)
            successful_reqs = sum(1 for r in runs if r.status in ["completed", "success"])
            failed_reqs = sum(1 for r in runs if r.status in ["failed", "rejected", "error"])

            # Map recent executions
            executions = []
            for r in runs[:20]:
                executions.append(
                    ExecutionRecordResponse(
                        request_id=r.run_id,
                        query=r.query,
                        status=r.status,
                        tools_used=r.tools_used or [],
                        duration_ms=r.duration_ms,
                        created_at=r.created_at,
                        execution_trace=r.execution_trace or [],
                    )
                )

            return UserUsageSummary(
                user_id=user_id,
                email=email,
                total_requests=total_reqs,
                successful_requests=successful_reqs,
                failed_requests=failed_reqs,
                total_input_tokens=int(in_tok or 0),
                total_output_tokens=int(out_tok or 0),
                total_tokens=int(tot_tok or 0),
                recent_executions=executions,
            )

    async def get_admin_all_users_summary(self) -> list[AdminUserSummary]:
        """Administrator view: Aggregate task and token consumption across all users."""
        summaries = []
        async with async_session_factory() as s:
            user_stmt = select(User).order_by(User.id)
            user_res = await s.execute(user_stmt)
            users = user_res.scalars().all()

            for u in users:
                # Count tasks
                run_count_stmt = select(func.count(AgentRun.id)).where(AgentRun.user_id == u.user_id)
                run_count = (await s.execute(run_count_stmt)).scalar() or 0

                # Sum tokens
                tok_sum_stmt = select(func.sum(TokenUsage.total_tokens)).where(TokenUsage.user_id == u.user_id)
                tok_sum = (await s.execute(tok_sum_stmt)).scalar() or 0

                summaries.append(
                    AdminUserSummary(
                        user_id=u.user_id,
                        email=u.email,
                        role=u.role,
                        status="Active" if u.is_active else "Disabled",
                        total_tasks=int(run_count),
                        total_tokens=int(tok_sum),
                        last_active=u.last_active,
                        created_at=u.created_at,
                    )
                )
        return summaries

    async def get_admin_overall_summary(self) -> AdminUsageOverallSummary:
        """Overall system overview for the admin dashboard header."""
        async with async_session_factory() as s:
            user_count = (await s.execute(select(func.count(User.id)))).scalar() or 0
            run_count = (await s.execute(select(func.count(AgentRun.id)))).scalar() or 0
            tok_count = (await s.execute(select(func.sum(TokenUsage.total_tokens)))).scalar() or 0

        from app.services.tool_registry import get_tool_registry
        tools_count = get_tool_registry().get_tool_count()

        return AdminUsageOverallSummary(
            total_users=int(user_count),
            total_executions=int(run_count),
            total_tokens_consumed=int(tok_count),
            active_tools_count=tools_count,
            indexed_documents_count=6,
        )


_token_usage_service: Optional[TokenUsageService] = None


def get_token_usage_service() -> TokenUsageService:
    global _token_usage_service
    if _token_usage_service is None:
        _token_usage_service = TokenUsageService()
    return _token_usage_service

