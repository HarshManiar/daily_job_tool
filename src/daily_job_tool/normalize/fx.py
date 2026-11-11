"""FX rates: ECB daily reference parser and fetcher, plus a dev-only fallback table."""

from datetime import date
from typing import TYPE_CHECKING
from xml.etree.ElementTree import Element

from defusedxml import ElementTree as ET

if TYPE_CHECKING:
    from daily_job_tool.http.client import CrawlClient

ECB_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"

# DEV-ONLY approximate rates (USD per 1 unit). Used when a NormContext is built without real
# rates (tests, local runs). Production must load fx_rates from the DB (`daily_job_tool fx fetch`).
FALLBACK_USD_PER_UNIT: dict[str, float] = {
    "USD": 1.0,
    "EUR": 1.08,
    "GBP": 1.27,
    "CAD": 0.73,
    "AUD": 0.65,
    "NZD": 0.60,
    "CHF": 1.13,
    "SEK": 0.095,
    "NOK": 0.093,
    "DKK": 0.145,
    "PLN": 0.25,
    "CZK": 0.043,
    "HUF": 0.0027,
    "RON": 0.22,
    "BGN": 0.55,
    "ISK": 0.0072,
    "TRY": 0.03,
    "JPY": 0.0066,
    "CNY": 0.14,
    "INR": 0.012,
    "SGD": 0.74,
    "HKD": 0.128,
    "KRW": 0.00073,
    "THB": 0.028,
    "MYR": 0.21,
    "IDR": 0.000062,
    "PHP": 0.017,
    "BRL": 0.19,
    "MXN": 0.055,
    "ZAR": 0.055,
    "ILS": 0.27,
    "AED": 0.272,
}


def _cubes(xml_text: str) -> list[Element]:
    root = ET.fromstring(xml_text)
    return [el for el in root.iter() if el.tag.rsplit("}", 1)[-1] == "Cube"]


def parse_ecb_xml(xml_text: str) -> dict[str, float]:
    """Parse the ECB eurofxref XML (units of currency per EUR) into USD-per-unit rates.

    Includes EUR (= the USD rate) and USD (= 1.0). Raises ValueError without a USD quote.
    """
    per_eur: dict[str, float] = {}
    for el in _cubes(xml_text):
        cur, rate = el.get("currency"), el.get("rate")
        if cur and rate:
            per_eur[cur.upper()] = float(rate)
    usd = per_eur.get("USD")
    if not usd:
        raise ValueError("ECB feed has no USD rate")
    out = {cur: usd / r for cur, r in per_eur.items() if r > 0}
    out["EUR"] = usd
    out["USD"] = 1.0
    return out


def parse_ecb_day(xml_text: str) -> date:
    for el in _cubes(xml_text):
        if el.get("time"):
            return date.fromisoformat(el.get("time", ""))
    raise ValueError("ECB feed has no date")


async def fetch_ecb_rates(client: "CrawlClient") -> tuple[date, dict[str, float]]:
    """Fetch the ECB daily file through the CrawlClient. Returns (publication day, rates)."""
    text = await client.get_text(ECB_URL)
    return parse_ecb_day(text), parse_ecb_xml(text)
