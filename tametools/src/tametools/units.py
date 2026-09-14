"""Bounded concentration conversions, not a general UCUM implementation."""
from decimal import Decimal, InvalidOperation, localcontext
from functools import lru_cache
import math
import re

from .cellstate import STATE_VALUE, cell_state


UNITS = {
    "g/L": ("gram / liter", "mass_concentration"),
    "g/dL": ("gram / deciliter", "mass_concentration"),
    "mg/L": ("milligram / liter", "mass_concentration"),
    "mg/dL": ("milligram / deciliter", "mass_concentration"),
    "ug/L": ("microgram / liter", "mass_concentration"),
    "ug/dL": ("microgram / deciliter", "mass_concentration"),
    "ug/mL": ("microgram / milliliter", "mass_concentration"),
    "ng/mL": ("nanogram / milliliter", "mass_concentration"),
    "pg/mL": ("picogram / milliliter", "mass_concentration"),
    "mol/L": ("mole / liter", "substance_concentration"),
    "mmol/L": ("millimole / liter", "substance_concentration"),
    "umol/L": ("micromole / liter", "substance_concentration"),
    "nmol/L": ("nanomole / liter", "substance_concentration"),
    "pmol/L": ("picomole / liter", "substance_concentration"),
    "U/L": ("enzyme_unit / liter", "catalytic_activity_concentration"),
    "kat/L": ("katal / liter", "catalytic_activity_concentration"),
    "ukat/L": ("microkatal / liter", "catalytic_activity_concentration"),
    "mEq/L": (None, "equivalent_concentration"),
    "Eq/L": (None, "equivalent_concentration"),
    "%": (None, "reported_percent"),
    "mmol/mol": (None, "substance_ratio"),
}
NUMBER = re.compile(r"^(?P<op><=|>=|<|>|=)?\s*(?P<value>[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)$")


@lru_cache(maxsize=1)
def _registry():
    try:
        import pint
    except ImportError as exc:
        raise ImportError("Install tametools[integration] (Pint) for unit integration") from exc
    return pint.UnitRegistry(non_int_type=Decimal)


def unit_property(unit):
    if not isinstance(unit, str) or unit not in UNITS:
        raise ValueError(f"UNSUPPORTED_UNIT {unit!r}: use an explicitly supported, case-sensitive unit code")
    return UNITS[unit][1]


@lru_cache(maxsize=128)
def unit_factor(source, target):
    if unit_property(source) != unit_property(target):
        raise ValueError("DIMENSION_CONFLICT: a reviewed analyte-specific bridge is required")
    if source == target:
        return Decimal(1)
    if unit_property(source) == "equivalent_concentration":
        return Decimal(1000) if source == "Eq/L" else Decimal("0.001")
    registry = _registry()
    quantity = registry.Quantity(Decimal(1), UNITS[source][0])
    return Decimal(str(quantity.to(UNITS[target][0]).magnitude))


def decimal_number(value, *, label="number"):
    if isinstance(value, bool):
        raise ValueError(f"{label}: boolean is not a concentration")
    text = str(value).strip()
    match = NUMBER.fullmatch(text)
    if not match or match["op"] or len(text) > 400:
        raise ValueError(f"{label}: expected a finite ordinary number")
    try:
        number = Decimal(match["value"])
        if not number.is_finite() or not math.isfinite(float(number)) or abs(number.adjusted()) > 300:
            raise ValueError(f"{label}: number is outside the supported finite range")
        return number
    except (InvalidOperation, OverflowError) as exc:
        raise ValueError(f"{label}: invalid number") from exc


def decimal_text(value):
    if value == 0:
        return "0"
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def convert_cell(value, factor, *, offset=0, comparators=True, label="result"):
    factor = decimal_number(factor, label="conversion factor")
    if factor <= 0:
        raise ValueError("Conversion factor must be positive")
    if cell_state(value) != STATE_VALUE:
        return value
    match = NUMBER.fullmatch(str(value).strip())
    if not match or (match["op"] and not comparators):
        raise ValueError(f"{label}: unsupported result or comparator")
    number = decimal_number(match["value"], label=label)
    with localcontext() as context:
        context.prec = 80
        converted = number * factor + decimal_number(offset, label="conversion offset")
    decimal_number(decimal_text(converted), label=label)
    return (match["op"] or "") + decimal_text(converted)
