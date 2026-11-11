import pytest

from firsthand.normalize import NormContext

# Fixed rates (USD per unit) so USD assertions are deterministic.
TEST_RATES = {
    "USD": 1.0,
    "EUR": 1.10,
    "GBP": 1.25,
    "CAD": 0.75,
    "AUD": 0.65,
    "NZD": 0.60,
    "CHF": 1.10,
    "SEK": 0.10,
    "NOK": 0.09,
    "DKK": 0.15,
    "PLN": 0.25,
    "CZK": 0.04,
    "INR": 0.012,
    "SGD": 0.75,
    "HKD": 0.13,
    "JPY": 0.0067,
    "KRW": 0.00075,
    "CNY": 0.14,
    "BRL": 0.20,
    "MXN": 0.055,
    "ZAR": 0.055,
    "AED": 0.27,
    "ILS": 0.27,
}


@pytest.fixture(scope="session")
def ctx() -> NormContext:
    return NormContext.load(TEST_RATES)
