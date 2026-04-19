from __future__ import annotations

from app.policy import indeed as indeed_policy
from app.policy.seek import BlockReason
from app.state.provider_job import ProviderJob
from app.state.provider_job_detail import ProviderJobDetail
from app.tools.client import ToolClient
from app.tools.indeed import search_indeed
from app.tools.indeed_detail import fetch_job_detail


class IndeedAdapter:
    async def search(
        self,
        client: ToolClient,
        *,
        keywords: str,
        location: str | None,
        max_pages: int,
    ) -> list[ProviderJob]:
        return await search_indeed(client, keywords=keywords, location=location, max_pages=max_pages)

    async def fetch_detail(self, client: ToolClient, job_id: str) -> ProviderJobDetail:
        return await fetch_job_detail(client, job_id=job_id)

    def is_blocked(self, job: ProviderJob) -> BlockReason | None:
        return indeed_policy.is_blocked(job)
