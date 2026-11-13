"""ADP Workforce Now recruiting adapter (tier 1; the public JSON behind ADP's career-centre SPA).

Token = the ``cid`` (client GUID from ``...recruitment.html?cid=<cid>&ccId=...``). Optional config:
``{"ccId": "19000101_000001"}`` (default, seen on every tenant so far), ``{"lang": "en_US"}``.

List: ``GET https://workforcenow.adp.com/mascsr/default/careercenter/public/events/staffing/v1/
job-requisitions?cid={cid}&ccId={ccId}&lang=en_US&$top=20&$skip=N`` -> ``{"jobRequisitions": [...],
"meta": {"totalNumber": N, "links": [filters]}}``. Items carry ``itemID`` (the id; also used in the
detail path), ``requisitionTitle``, ``postDate`` (naive ISO timestamp, treated as UTC),
``requisitionLocations`` (``nameCode.shortName``, e.g. " Fremont, CA, US"),
``workLevelCode.shortName``
(employment type / worker category), ``payGradeRange.{minimumRate,maximumRate}.amountValue`` and
``customFieldGroup`` (``ExternalJobID`` string field, ``SalaryRange`` text, ``PostingDate`` date).
Pay is in the LIST; the description is only in the detail, so every job is ``needs_detail``.
Paginate with ``$skip`` until an empty page or ``skip >= totalNumber``.

Detail: same path plus ``/{itemID}`` and the same query -> the item plus ``requisitionDescription``
(HTML). An unknown id returns HTTP 200 with an empty shell (no ``requisitionTitle``); that is
mapped to NotFound.

Quirks: ADP's ``SalaryType`` says "Annually" even for hourly figures ($25-$40), so the period is
inferred from magnitude (max < 1000 -> hour, else year). ``customFieldGroup`` holds volatile
``CurrentServerDateTime`` and ``ApplicantCount`` values, so ``numberFields`` and
``CurrentServerDateTime`` are dropped from the stored payload (they would change the hash daily).
Only ``requisitionDescription`` is kept from the detail. ``complete`` requires distinct ids +
skipped records >= totalNumber. ADP's robots.txt is an HTML redirector (no rules); keep it polite.
``_map`` rebuilds a RawJob from the stored payload (``_cid``/``_ccId`` context keys) with no HTTP.
"""

import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, ClassVar
from urllib.parse import parse_qs, urlsplit

from daily_job_tool.adapters.base import Adapter, FetchResult, RawJob, SalaryRaw, SourceRef
from daily_job_tool.adapters.registry import register
from daily_job_tool.http.errors import NotFound

if TYPE_CHECKING:
    from daily_job_tool.http.client import CrawlClient

HOSTS = ("workforcenow.adp.com", "recruiting.adp.com")
BASE = "https://workforcenow.adp.com/mascsr/default/careercenter/public/events/staffing/v1"
PAGE_SIZE = 20
MAX_PAGES = 100
DEFAULT_CCID = "19000101_000001"
CID_RE = re.compile(r"^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$", re.IGNORECASE)
VOLATILE_DATES = {"CurrentServerDateTime"}


def _params(src: SourceRef) -> tuple[str, str, str]:
    c = src.config
    return (
        str(c.get("cid") or src.token),
        str(c.get("ccId") or DEFAULT_CCID),
        str(c.get("lang") or "en_US"),
    )


def _query(cid: str, cc: str, lang: str) -> str:
    return f"cid={cid}&ccId={cc}&lang={lang}"


def _clean(rec: dict[str, Any]) -> dict[str, Any]:
    """Drop volatile values (server clock, applicant counts) so the payload hash is stable."""
    out = dict(rec)
    cfg = out.get("customFieldGroup")
    if isinstance(cfg, dict):
        cfg = {k: v for k, v in cfg.items() if k != "numberFields"}
        dates = cfg.get("dateFields")
        if isinstance(dates, list):
            cfg["dateFields"] = [
                x
                for x in dates
                if not (
                    isinstance(x, dict)
                    and (x.get("nameCode") or {}).get("codeValue") in VOLATILE_DATES
                )
            ]
        out["customFieldGroup"] = cfg
    return out


def _field(rec: dict[str, Any], group: str, code: str, key: str) -> Any:
    cfg = rec.get("customFieldGroup")
    for f in (cfg.get(group) if isinstance(cfg, dict) else None) or []:
        if isinstance(f, dict) and (f.get("nameCode") or {}).get("codeValue") == code:
            return f.get(key)
    return None


def _dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        d = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d.replace(tzinfo=UTC) if d.tzinfo is None else d.astimezone(UTC)


def _amount(rate: Any) -> tuple[float | None, str | None]:
    if not isinstance(rate, dict):
        return None, None
    v = rate.get("amountValue")
    ok = isinstance(v, int | float) and not isinstance(v, bool)
    return (float(v) if ok else None), rate.get("currencyCode")


def _salary(rec: dict[str, Any]) -> SalaryRaw | None:
    pay = rec.get("payGradeRange")
    if not isinstance(pay, dict):
        return None
    lo, cur_lo = _amount(pay.get("minimumRate"))
    hi, cur_hi = _amount(pay.get("maximumRate"))
    if lo is None and hi is None:
        return None
    top = hi if hi is not None else lo
    text = _field(rec, "stringFields", "SalaryRange", "stringValue")
    return SalaryRaw(
        min=lo,
        max=hi,
        currency=cur_lo or cur_hi,
        period="hour" if top is not None and top < 1000 else "year",
        text=text if isinstance(text, str) else None,
    )


def _locations(rec: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for loc in rec.get("requisitionLocations") or []:
        if not isinstance(loc, dict):
            continue
        text = (loc.get("nameCode") or {}).get("shortName")
        if not (isinstance(text, str) and text.strip()):
            addr = loc.get("address") or {}
            sub = (addr.get("countrySubdivisionLevel1") or {}).get("codeValue")
            text = ", ".join(x for x in (addr.get("cityName"), sub) if x)
        text = text.strip() if isinstance(text, str) else ""
        if text and text not in out:
            out.append(text)
    return out


@register
class AdpAdapter(Adapter):
    name: ClassVar[str] = "adp"
    tier: ClassVar[int] = 1
    hosts: ClassVar[list[str]] = list(HOSTS)

    async def fetch_jobs(self, src: SourceRef, http: "CrawlClient") -> FetchResult:
        cid, cc, lang = _params(src)
        base = f"{BASE}/job-requisitions?{_query(cid, cc, lang)}&$top={PAGE_SIZE}"
        jobs: list[RawJob] = []
        warnings: list[str] = []
        seen: set[str] = set()
        skipped = 0
        total: int | None = None
        skip = 0
        for _ in range(MAX_PAGES):
            data = await http.get_json(f"{base}&$skip={skip}")
            recs = data.get("jobRequisitions") if isinstance(data, dict) else None
            if not isinstance(recs, list):
                raise ValueError(f"adp {src.token}: unexpected list payload")
            meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
            n = meta.get("totalNumber")
            if isinstance(n, int) and not isinstance(n, bool):
                total = n
            if not recs:
                break
            for rec in recs:
                job = (
                    self._map({**_clean(rec), "_cid": cid, "_ccId": cc, "_lang": lang})
                    if isinstance(rec, dict)
                    else None
                )
                if job is None:
                    skipped += 1
                    warnings.append(f"skipped malformed record: {str(rec)[:120]}")
                elif job.external_id not in seen:
                    seen.add(job.external_id)
                    jobs.append(job)
            skip += len(recs)
            if total is not None and skip >= total:
                break
        else:
            warnings.append("page cap reached; board not fully enumerated")
        complete = total is not None and len(seen) + skipped >= total
        if total is not None and not complete:
            warnings.append(f"enumerated {len(seen)} of {total} jobs")
        return FetchResult(jobs=jobs, complete=complete, warnings=warnings)

    async def fetch_detail(self, src: SourceRef, job: RawJob, http: "CrawlClient") -> RawJob:
        cid, cc, lang = _params(src)
        data = await http.get_json(
            f"{BASE}/job-requisitions/{job.external_id}?{_query(cid, cc, lang)}"
        )
        if not isinstance(data, dict):
            raise ValueError(f"adp {src.token}: unexpected detail payload")
        if not data.get("requisitionTitle"):  # unknown ids return a 200 empty shell
            raise NotFound(f"{BASE}/job-requisitions/{job.external_id}")
        desc = data.get("requisitionDescription")
        detail = {"requisitionDescription": desc} if isinstance(desc, str) else {}
        mapped = self._map({**job.payload, "detail": detail})
        return mapped if mapped is not None else job

    @staticmethod
    def _map(payload: Any) -> RawJob | None:
        if not isinstance(payload, dict):
            return None
        jid, title, cid = (
            payload.get("itemID"),
            payload.get("requisitionTitle"),
            payload.get("_cid"),
        )
        if not jid or not isinstance(title, str) or not title.strip() or not cid:
            return None
        cc = payload.get("_ccId") or DEFAULT_CCID
        lang = payload.get("_lang") or "en_US"
        ext = _field(payload, "stringFields", "ExternalJobID", "stringValue")
        page = "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html"
        url = f"{page}?{_query(cid, cc, lang)}"
        if ext:
            url += f"&jobId={ext}"
        detail = payload.get("detail") if isinstance(payload.get("detail"), dict) else None
        desc = (detail or {}).get("requisitionDescription")
        level = payload.get("workLevelCode")
        etype = level.get("shortName") if isinstance(level, dict) else None
        locs = _locations(payload)
        remote = any(loc.lower().startswith("remote") for loc in locs)
        return RawJob(
            external_id=str(jid),
            title=title.strip(),
            url=url,
            apply_url=url,
            locations_raw=locs,
            workplace_raw="remote" if remote else None,
            employment_type_raw=etype if isinstance(etype, str) and etype.strip() else None,
            description_html=desc if isinstance(desc, str) and desc.strip() else None,
            posted_at=_dt(payload.get("postDate"))
            or _dt(_field(payload, "dateFields", "PostingDate", "dateValue")),
            salary=_salary(payload),
            needs_detail=detail is None,
            payload=payload,
        )

    @classmethod
    def match_url(cls, url: str) -> tuple[str, dict[str, Any]] | None:
        try:
            p = urlsplit(url.strip())
        except ValueError:
            return None
        if (p.hostname or "").lower() not in HOSTS:
            return None
        q = {k.lower(): v[0] for k, v in parse_qs(p.query).items() if v}
        cid = q.get("cid")
        if not cid or not CID_RE.match(cid):
            return None
        cc = q.get("ccid")
        return cid, ({"ccId": cc} if cc and cc != DEFAULT_CCID else {})
