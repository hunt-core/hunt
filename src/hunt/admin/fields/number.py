from __future__ import annotations

from typing import Any

from hunt.admin.field import Field


class Number(Field):
    field_type: str = "number"

    def __init__(self, name: str, attribute: str | None = None) -> None:
        super().__init__(name, attribute)
        self._min: int | None = None
        self._max: int | None = None

    def min(self, n: int) -> Number:
        self._min = n
        return self

    def max(self, n: int) -> Number:
        self._max = n
        return self

    def from_form_value(self, value: Any, current: Any = None) -> Any:
        if value is None or value == "":
            return None
        if isinstance(value, str):
            text = value.strip()
            try:
                return int(text)
            except ValueError:
                try:
                    return float(text)
                except ValueError:
                    return value
        return value


_CURRENCY_SYMBOLS = {
    "USD": "$",
    "EUR": "\u20ac",
    "GBP": "\u00a3",
    "JPY": "\u00a5",
    "CNY": "\u00a5",
    "INR": "\u20b9",
    "KRW": "\u20a9",
    "CHF": "CHF ",
    "CAD": "CA$",
    "AUD": "A$",
    "NZD": "NZ$",
    "BRL": "R$",
    "ZAR": "R ",
    "SEK": "kr ",
    "NOK": "kr ",
    "DKK": "kr ",
    "PLN": "z\u0142 ",
}
_ZERO_DECIMAL_CURRENCIES = frozenset({"JPY", "KRW"})


class Currency(Field):
    field_type: str = "currency"

    def __init__(self, name: str, attribute: str | None = None) -> None:
        super().__init__(name, attribute)
        self._currency: str = "USD"

    def currency(self, code: str) -> Currency:
        self._currency = code
        return self

    def from_form_value(self, value: Any, current: Any = None) -> Any:
        if value is None or value == "":
            return None
        return value

    def display_value(self, instance: Any) -> str:
        val = self.value_for(instance)
        if val is None:
            return ""
        try:
            amount = float(val)
        except (ValueError, TypeError):
            return str(val)
        code = str(self._currency or "USD").upper()
        digits = 0 if code in _ZERO_DECIMAL_CURRENCIES else 2
        symbol = _CURRENCY_SYMBOLS.get(code)
        if symbol is None:
            symbol = f"{code} "
        return f"{symbol}{amount:.{digits}f}"
