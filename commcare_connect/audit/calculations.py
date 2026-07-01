from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar

_REGISTRY: list[AuditCalculation] = []


@dataclass
class Measurement:
    """Raw result of a calculation's ``compute()``: a ``value`` over a sample.

    ``sample_size`` is the gating quantity compared against ``min_sample_size``.
    ``denominator`` overrides the displayed fraction's denominator for percentage
    calculations whose rate is taken over a different population than the gate;
    when ``None`` the denominator defaults to ``sample_size``.
    ``components`` exposes the sub-fractions of a value that is itself built from
    several fractions (e.g. a ratio-of-ratios); each entry is a
    ``{"numerator", "denominator"}`` dict, rendered in order alongside the value.
    """

    value: Any
    sample_size: int
    denominator: int | None = None
    components: list[dict] | None = None


@dataclass
class CalculationResult:
    name: str
    label: str
    value: Any
    has_sufficient_data: bool
    in_range: bool
    numerator: int | None = None
    denominator: int | None = None
    components: list[dict] | None = None

    def to_dict(self) -> dict:
        d = {
            "value": self.value,
            "has_sufficient_data": self.has_sufficient_data,
            "in_range": self.in_range,
            "label": self.label,
        }
        if self.numerator is not None:
            d["numerator"] = self.numerator
        if self.denominator is not None:
            d["denominator"] = self.denominator
        if self.components is not None:
            d["components"] = self.components
        return d


class AuditCalculation(ABC):
    """Base class for audit calculations.

    Subclasses declare a ``name`` and ``label`` and implement :meth:`compute`,
    returning ``(value, sample_size)``. The base class handles the
    "insufficient data" short-circuit and the bounds check, so concrete
    subclasses don't repeat that boilerplate.

    Tuning knobs (override as class attributes):

    - ``min_sample_size``: if the reported sample size is less than this,
      ``has_sufficient_data`` is False and the result is considered in-range
      by convention (no actionable data).
    - ``lower_bound`` / ``upper_bound``: inclusive acceptability range for
      the value (``lower_bound <= value <= upper_bound``). Either may be
      ``None`` to disable that side.
    """

    name: ClassVar[str]
    label: ClassVar[str]
    tooltip: ClassVar[str] = ""
    is_percentage: ClassVar[bool] = False
    min_sample_size: ClassVar[int] = 1
    lower_bound: ClassVar[float | None] = None
    upper_bound: ClassVar[float | None] = None

    @abstractmethod
    def compute(self, opportunity_access, period_start, period_end) -> Measurement:
        """Return a :class:`Measurement`. ``value`` may be ``None`` when
        ``sample_size == 0``. Set ``denominator`` only when a percentage's rate
        is taken over a different population than the gating ``sample_size``.
        """

    def run(self, opportunity_access, period_start, period_end) -> CalculationResult:
        m = self.compute(opportunity_access, period_start, period_end)
        has_sufficient_data = m.sample_size >= self.min_sample_size
        if not has_sufficient_data:
            return CalculationResult(
                name=self.name,
                label=self.label,
                value=None,
                has_sufficient_data=False,
                in_range=True,
            )
        numerator = None
        denominator = None
        if self.is_percentage and m.value is not None:
            # Denominator defaults to the gating sample unless compute() overrode
            # it; the numerator is derived from value so the two never diverge.
            denominator = m.sample_size if m.denominator is None else m.denominator
            numerator = round(m.value * denominator / 100)
        return CalculationResult(
            name=self.name,
            label=self.label,
            value=m.value,
            has_sufficient_data=True,
            in_range=self._in_range(m.value),
            numerator=numerator,
            denominator=denominator,
            components=m.components,
        )

    def _in_range(self, value) -> bool:
        if self.lower_bound is not None and value < self.lower_bound:
            return False
        if self.upper_bound is not None and value > self.upper_bound:
            return False
        return True


def format_value(result: dict, with_fraction: bool = False) -> str:
    """Format a calculation result dict for display. Reads ``value`` and,
    for percentages, ``numerator``/``denominator``; for values built from
    sub-fractions, ``components``."""
    value = result.get("value")
    if value is None:
        return "-"
    components = result.get("components")
    if components:
        return f"{_format_scalar(value)} ({_format_components(components)})"
    if result.get("numerator") is not None:
        if with_fraction:
            return f"{round(value)}% ({result['numerator']}/{result['denominator']})"
        return f"{round(value)}%"
    return _format_scalar(value)


def _format_scalar(value) -> str:
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def _format_components(components: list[dict]) -> str:
    return " / ".join(f"({c['numerator']}/{c['denominator']})" for c in components)


def register_calculation(cls):
    """Class decorator: instantiate ``cls`` once and add the instance to the registry."""
    _REGISTRY.append(cls())
    return cls


def get_registered_calculations() -> list[AuditCalculation]:
    return list(_REGISTRY)
