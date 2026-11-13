"""Idempotent seed import: data/seeds.csv -> companies + sources."""

import csv
import json
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from daily_job_tool.adapters import registry as adapter_registry
from daily_job_tool.http.blocklist import is_blocked_host
from daily_job_tool.match.engine import _co_key

COLUMNS = (
    "name,domain,careers_url,adapter,token,hq_country,size_bucket,tags,industry,"
    "is_staffing,exclude_reason,config"
).split(",")
SIZE_BUCKETS = {"1-10", "11-50", "51-200", "201-1000", "1001-5000", "5000+"}
TWO_LEVEL_SUFFIXES = frozenset(
    {
        "co.uk", "org.uk", "ac.uk", "com.au", "net.au", "co.jp", "co.nz", "co.in",
        "com.br", "co.za", "com.sg", "com.cn", "com.mx", "co.kr",
    }
)  # fmt: skip
SEED_SOURCE = "manual:seeds.csv"
_TRUE = {"1", "true", "t", "yes", "y"}


def normalize_domain(value: str) -> str | None:
    """Registrable lowercase domain from a URL or host ('https://www.a.b.co.uk/x' -> 'b.co.uk')."""
    v = (value or "").strip().lower()
    if not v:
        return None
    if "://" not in v:
        v = "//" + v
    host = (urlsplit(v).hostname or "").rstrip(".")
    if not host or "." not in host:
        return None
    labels = host.split(".")
    if host.startswith("www."):
        labels = labels[1:]
    keep = 3 if ".".join(labels[-2:]) in TWO_LEVEL_SUFFIXES else 2
    return ".".join(labels[-keep:])


def slugify(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s.replace("&", " and ")).strip("-")


@dataclass
class ImportReport:
    companies_new: int = 0
    companies_updated: int = 0
    companies_unchanged: int = 0
    sources_new: int = 0
    sources_updated: int = 0
    sources_unchanged: int = 0
    rejected: list[tuple[int, str, str]] = field(default_factory=list)  # (line, name, reason)
    rescored_companies: set[int] = field(default_factory=set)  # match-relevant company edits

    def render(self) -> str:
        lines = [
            f"companies: {self.companies_new} new, {self.companies_updated} updated, "
            f"{self.companies_unchanged} unchanged",
            f"sources:   {self.sources_new} new, {self.sources_updated} updated, "
            f"{self.sources_unchanged} unchanged",
            f"rejected:  {len(self.rejected)}",
        ]
        lines += [f"  line {ln}: {name or '?'}: {why}" for ln, name, why in self.rejected]
        return "\n".join(lines)


async def _blocked_in_db(conn: AsyncConnection) -> frozenset[str]:
    rows = (await conn.execute(text("SELECT lower(domain::text) FROM blocked_domains"))).scalars()
    return frozenset(rows)


def _split_tags(v: str) -> list[str]:
    return [t.strip() for t in v.split(";") if t.strip()]


def _read_rows(path: str | Path) -> list[tuple[int, dict[str, str]]]:
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        missing = {"name"} - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path}: missing required column(s) {sorted(missing)}")
        return [
            (i, {k: (v or "").strip() for k, v in r.items() if k}) for i, r in enumerate(reader, 2)
        ]


def _row_key(row: dict[str, str]) -> str:
    return normalize_domain(row.get("domain", "")) or "name:" + slugify(row.get("name", ""))


def _merge_local(
    base: list[tuple[int, dict[str, str]]], local: list[tuple[int, dict[str, str]]]
) -> list[tuple[int, dict[str, str]]]:
    """Local rows replace base rows with the same domain (or same name when no domain)."""
    local_keys = {_row_key(r) for _, r in local if r.get("name")}
    kept = [(ln, r) for ln, r in base if not r.get("name") or _row_key(r) not in local_keys]
    return kept + local


async def import_seeds(
    engine: AsyncEngine,
    csv_path: str | Path,
    *,
    local_path: str | Path | None = None,
    exclude_companies: Sequence[str] = (),
) -> ImportReport:
    """Import seeds.csv, then merge an optional local CSV (missing file is fine).

    `exclude_companies` (profile hard_filters.exclude_companies): matching companies get an
    exclude_reason unless the row already has one, so no employer name has to live in the CSV.
    """
    report = ImportReport()
    rows = _read_rows(csv_path)
    if local_path is not None and Path(local_path).exists():
        rows = _merge_local(rows, _read_rows(local_path))
    excluded_keys = {_co_key(c) for c in exclude_companies if c and _co_key(c)}
    if excluded_keys:
        for _, row in rows:
            if (
                row.get("name")
                and not row.get("exclude_reason")
                and _co_key(row["name"]) in excluded_keys
            ):
                row["exclude_reason"] = "excluded by profile (current employer)"
    async with engine.begin() as conn:
        db_blocked = await _blocked_in_db(conn)
        for line, row in rows:
            if not any(row.values()):
                continue
            try:
                await _import_row(conn, row, report, db_blocked)
            except ValueError as exc:
                report.rejected.append((line, row.get("name", ""), str(exc)))
        if report.rescored_companies:
            # company fields feed the match engine: refresh that company's open jobs now
            from daily_job_tool.pipeline.renormalize import rescore_conn

            for cid in sorted(report.rescored_companies):
                await rescore_conn(conn, company_id=cid)
    return report


def _is_blocked(host: str, db_blocked: frozenset[str]) -> bool:
    return is_blocked_host(host) or is_blocked_host(host, db_blocked)


async def _import_row(
    conn: AsyncConnection, row: dict[str, str], report: ImportReport, db_blocked: frozenset[str]
) -> None:
    name = row.get("name", "")
    if not name:
        raise ValueError("missing name")
    domain = normalize_domain(row.get("domain", ""))
    if row.get("domain") and domain is None:
        raise ValueError(f"unparseable domain {row['domain']!r}")
    careers_url = row.get("careers_url") or None
    for host in {
        domain,
        urlsplit(
            row.get("domain", "")
            if "://" in row.get("domain", "")
            else "//" + row.get("domain", "")
        ).hostname,
        urlsplit(careers_url).hostname if careers_url else None,
    }:
        if host and _is_blocked(host, db_blocked):
            raise ValueError(f"blocked domain {host}")
    country = row.get("hq_country", "").upper() or None
    if country and not re.fullmatch(r"[A-Z]{2}", country):
        raise ValueError(f"bad hq_country {country!r}")
    size = row.get("size_bucket") or None
    if size and size not in SIZE_BUCKETS:
        raise ValueError(f"bad size_bucket {size!r}")
    adapter, token = row.get("adapter", ""), row.get("token", "")
    if bool(adapter) != bool(token):
        raise ValueError("adapter and token must be given together")
    config: dict[str, Any] = {}
    if row.get("config"):
        try:
            config = json.loads(row["config"])
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid config JSON: {exc}") from exc
        if not isinstance(config, dict):
            raise ValueError("config must be a JSON object")
    if adapter:
        try:
            adapter_registry.get_adapter(adapter)
        except KeyError as exc:
            raise ValueError(f"unknown adapter {adapter!r}") from exc

    want: dict[str, Any] = {
        "name": name,
        "careers_url": careers_url,
        "hq_country": country,
        "size_bucket": size,
        "tags": _split_tags(row.get("tags", "")),
        "industry": row.get("industry") or None,
        "is_staffing": row.get("is_staffing", "").lower() in _TRUE,
        "exclude_reason": row.get("exclude_reason") or None,
    }
    slug = slugify(name)
    if not slug:
        raise ValueError("name has no usable characters")

    sel = (
        "SELECT id, slug::text AS slug, domain::text AS domain, name, careers_url, hq_country, "
        "size_bucket, tags, industry, is_staffing, exclude_reason, status::text AS status "
        "FROM companies WHERE "
    )
    existing = None
    if domain:
        existing = (await conn.execute(text(sel + "domain = :d"), {"d": domain})).first()
    if existing is None and not domain:
        existing = (
            await conn.execute(text(sel + "slug = :s AND domain IS NULL"), {"s": slug})
        ).first()
    if existing is not None and existing.status == "removed":
        # removal requests are honored permanently: never update or reactivate
        raise ValueError(f"company {existing.name!r} was removed on request")
    if existing is None:
        taken = (
            await conn.execute(
                text("SELECT domain::text FROM companies WHERE slug = :s"), {"s": slug}
            )
        ).first()
        if taken is not None:
            if domain is None:
                raise ValueError(f"slug {slug!r} already used by a company with a domain")
            slug = f"{slug}-{domain.split('.')[0]}"
            if (
                await conn.execute(text("SELECT 1 FROM companies WHERE slug = :s"), {"s": slug})
            ).first():
                raise ValueError(f"slug {slug!r} already taken")
        cid = (
            await conn.execute(
                text(
                    "INSERT INTO companies (name, slug, domain, careers_url, hq_country, "
                    "size_bucket, tags, industry, is_staffing, exclude_reason, seed_source) "
                    "VALUES (:name, :slug, :domain, :careers_url, :hq_country, :size_bucket, "
                    ":tags, :industry, :is_staffing, :exclude_reason, :seed) RETURNING id"
                ),
                {**want, "slug": slug, "domain": domain, "seed": SEED_SOURCE},
            )
        ).scalar_one()
        report.companies_new += 1
    else:
        cid = existing.id
        changed = [k for k, v in want.items() if getattr(existing, k) != v]
        if changed:
            sets = ", ".join(f"{k} = :{k}" for k in changed)
            await conn.execute(
                text(f"UPDATE companies SET {sets}, updated_at = now() WHERE id = :id"),  # noqa: S608
                {**want, "id": cid},
            )
            report.companies_updated += 1
            if {"name", "industry", "is_staffing", "exclude_reason"} & set(changed):
                report.rescored_companies.add(cid)
        else:
            report.companies_unchanged += 1

    if adapter:
        src = (
            await conn.execute(
                text(
                    "SELECT id, company_id, config FROM sources WHERE adapter = :a AND token = :t"
                ),
                {"a": adapter, "t": token},
            )
        ).first()
        if src is None:
            await conn.execute(
                text(
                    "INSERT INTO sources (company_id, adapter, token, config, detected_by) "
                    "VALUES (:c, :a, :t, CAST(:cfg AS jsonb), 'manual')"
                ),
                {"c": cid, "a": adapter, "t": token, "cfg": json.dumps(config)},
            )
            report.sources_new += 1
        elif src.company_id != cid:
            raise ValueError(f"source {adapter}/{token} already belongs to another company")
        elif (src.config or {}) != config:
            await conn.execute(
                text("UPDATE sources SET config = CAST(:cfg AS jsonb) WHERE id = :i"),
                {"cfg": json.dumps(config), "i": src.id},
            )
            report.sources_updated += 1
        else:
            report.sources_unchanged += 1
