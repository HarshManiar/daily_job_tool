"""Batch runner for `daily_job_tool detect`: CSV in, CSV out, resume-safe."""

import asyncio
import csv
from pathlib import Path

from daily_job_tool.detect.detector import Detection, detect_company
from daily_job_tool.http.client import CrawlClient

OUT_FIELDS = [
    "name",
    "domain",
    "industry",
    "ats",
    "token",
    "careers_url",
    "status",
    "confidence",
    "jsonld_jobposting",
    "evidence",
    "notes",
]


def row_for(name: str, domain: str, industry: str, d: Detection) -> dict[str, str]:
    return {
        "name": name,
        "domain": domain,
        "industry": industry,
        "ats": d.ats,
        "token": d.token or "",
        "careers_url": d.careers_url or "",
        "status": d.status,
        "confidence": f"{d.confidence:.2f}",
        "jsonld_jobposting": str(d.jsonld_jobposting).lower(),
        "evidence": d.evidence,
        "notes": d.notes,
    }


async def run_batch(
    csv_path: Path,
    out_path: Path,
    *,
    limit: int | None = None,
    concurrency: int = 4,
    client: CrawlClient | None = None,
) -> dict[str, int]:
    with csv_path.open(newline="") as f:
        rows = [r for r in csv.DictReader(f) if (r.get("domain") or "").strip()]
    done: set[str] = set()
    if out_path.exists():
        with out_path.open(newline="") as f:
            done = {r["domain"] for r in csv.DictReader(f)}
    todo = [r for r in rows if r["domain"].strip() not in done]
    if limit is not None:
        todo = todo[:limit]
    new_file = not out_path.exists() or out_path.stat().st_size == 0
    out_path.parent.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    own = client is None
    if client is None:
        from daily_job_tool.config import get_user_agent

        client = CrawlClient(get_user_agent())
    sem = asyncio.Semaphore(concurrency)
    lock = asyncio.Lock()
    with out_path.open("a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=OUT_FIELDS)
        if new_file:
            w.writeheader()

        async def one(r: dict[str, str]) -> None:
            async with sem:
                d = await detect_company(client, r["domain"], {"name": r.get("name", "")})
            async with lock:
                w.writerow(
                    row_for(r.get("name", ""), r["domain"].strip(), r.get("industry", ""), d)
                )
                fh.flush()
                counts[d.status] = counts.get(d.status, 0) + 1

        try:
            await asyncio.gather(*(one(r) for r in todo))
        finally:
            if own:
                await client.aclose()
    return counts
