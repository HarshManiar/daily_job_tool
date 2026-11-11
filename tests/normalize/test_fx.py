import pytest

from daily_job_tool.normalize.fx import FALLBACK_USD_PER_UNIT, parse_ecb_day, parse_ecb_xml

ECB_XML = """<?xml version="1.0" encoding="UTF-8"?>
<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01"
 xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">
<gesmes:subject>Reference rates</gesmes:subject>
<gesmes:Sender><gesmes:name>European Central Bank</gesmes:name></gesmes:Sender>
<Cube><Cube time='2026-11-11'>
<Cube currency='USD' rate='1.1000'/>
<Cube currency='JPY' rate='165.00'/>
<Cube currency='GBP' rate='0.8800'/>
<Cube currency='INR' rate='95.0000'/>
</Cube></Cube></gesmes:Envelope>"""


def test_parse_ecb_xml_usd_per_unit():
    r = parse_ecb_xml(ECB_XML)
    assert r["USD"] == 1.0
    assert r["EUR"] == pytest.approx(1.10)
    assert r["GBP"] == pytest.approx(1.25)
    assert r["JPY"] == pytest.approx(1.1 / 165)
    assert r["INR"] == pytest.approx(1.1 / 95)
    assert set(r) == {"USD", "EUR", "GBP", "JPY", "INR"}


def test_parse_ecb_day():
    assert str(parse_ecb_day(ECB_XML)) == "2026-11-11"


def test_parse_ecb_requires_usd():
    xml = ECB_XML.replace("<Cube currency='USD' rate='1.1000'/>", "")
    with pytest.raises(ValueError):
        parse_ecb_xml(xml)


def test_fallback_table_covers_common_currencies():
    for c in ("USD", "EUR", "GBP", "CAD", "AUD", "INR", "JPY", "CHF", "SEK", "SGD", "BRL", "PLN"):
        assert FALLBACK_USD_PER_UNIT[c] > 0
    assert len(FALLBACK_USD_PER_UNIT) >= 25
