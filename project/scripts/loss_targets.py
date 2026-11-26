"""Actual five MARKET-session endpoint loss, not FF3 residual or drawdown.

This module deliberately certifies neither the listing universe nor DRET's total
return convention. A finite, complete raw-return window is a CANDIDATE outcome.
Missing observations never mean zero return, safety, suspension or delisting.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
from typing import Mapping, Sequence

HORIZON = 5
TARGET_VERSION = "actual-loss-r5-market-endpoint-observed-unverified-v1"
UNIVERSE_STATUS = "OBSERVED_UNVERIFIED"
TARGET_CONTRACT = {
    "version": TARGET_VERSION,
    "horizon_market_sessions": HORIZON,
    "formula": "product(1+Dret[t+k],k=1..5)-1",
    "return_input": "raw_Dret_decimal_no_rf_market_industry_or_factor_subtraction",
    "loss_bins": ["R>=0", "-0.05<R<0", "-0.10<R<=-0.05", "R<=-0.10"],
    "events": {"label_loss": "R<0", "label_loss_5pct": "R<=-0.05", "label_loss_10pct": "R<=-0.10"},
    "universe": "ever_observed_at_or_before_t_carried_forward_without_future_last_date_filter",
    "certification": "candidate_only_listing_identity_tradability_terminal_and_total_return_unverified",
    "missing_policy": "nullable_unknown_no_suspension_zero_no_next_stock_row_no_delisting_minus_one",
    "endpoint": "fifth_market_session_not_intrahorizon_drawdown_or_terminal_liquidation_price",
}
NULL_OUTCOME_FIELDS = ("forward_5d_return", "loss_bin", "label_loss", "label_loss_5pct", "label_loss_10pct")


def canonical_day(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError("Noncanonical ISO market/source date")
    return value


def source_key(stock, day):
    # Never normalize or truncate identifiers: this key must match the source.
    if not isinstance(stock, str) or not re.fullmatch(r"[0-9]{6}", stock):
        raise ValueError("Source stock key must be exactly six ASCII digits")
    return stock, canonical_day(day)


@dataclass(frozen=True)
class ReturnObservation:
    raw: str
    value: Decimal | None
    state: str
    source_ordinal: int = 0


def parse_return(raw, source_ordinal=0):
    text = "" if raw is None else str(raw)
    if not text.strip():
        return ReturnObservation(text, None, "missing_return", source_ordinal)
    try:
        value = Decimal(text.strip())
    except InvalidOperation:
        return ReturnObservation(text, None, "invalid_return", source_ordinal)
    if not value.is_finite() or not math.isfinite(float(value)):
        return ReturnObservation(text, None, "nonfinite_return", source_ordinal)
    if value < -1:
        return ReturnObservation(text, None, "below_minus_one", source_ordinal)
    return ReturnObservation(text, value, "valid", source_ordinal)


def labels_from_return(value):
    value = Decimal(str(value)) if not isinstance(value, Decimal) else value
    if not value.is_finite() or not math.isfinite(float(value)) or value < -1:
        raise ValueError("Endpoint return must be finite and >= -1")
    loss_bin = 0 if value >= 0 else 1 if value > Decimal("-.05") else 2 if value > Decimal("-.10") else 3
    return {"loss_bin": loss_bin, "label_loss": int(value < 0),
            "label_loss_5pct": int(value <= Decimal("-.05")), "label_loss_10pct": int(value <= Decimal("-.10"))}


def compound_five(values):
    """Decimal threshold arithmetic avoids losing exact -5%/-10% equality.

    Inputs are already validated decimal fractional returns. Precision scales
    with the input coefficients so five finite-decimal factors multiply exactly.
    No epsilon widens a bin and there is no future risk-free/factor requirement.
    """
    if len(values) != HORIZON:
        raise ValueError("Exactly five market-session returns are required")
    for value in values:
        if value is None or not value.is_finite() or not math.isfinite(float(value)) or value < -1:
            raise ValueError("Invalid economic raw return")
    # Adding one to a tiny decimal can require its leading fractional zeros.
    precision = 8 + sum(max(len(v.as_tuple().digits), 1 + abs(v.as_tuple().exponent)) for v in values)
    with localcontext() as ctx:
        ctx.prec = max(64, precision)
        wealth = Decimal(1)
        for value in values:
            wealth *= 1 + value
        result = wealth - 1
    if not result.is_finite() or not math.isfinite(float(result)) or result < -1:
        raise ValueError("Nonfinite or impossible compounded economic return")
    return result


def outcome_at(sessions: Sequence[str], origin_position: int,
               observations: Mapping[str, ReturnObservation], source_end: str | None = None):
    if not 0 <= origin_position < len(sessions):
        raise ValueError("Origin outside market calendar")
    future = sessions[origin_position + 1:origin_position + HORIZON + 1]
    missing_observation = missing_return = invalid_return = 0
    values = []
    for day in future:
        observation = observations.get(day)
        if observation is None:
            missing_observation += 1
        elif observation.state == "missing_return":
            missing_return += 1
        elif observation.state != "valid":
            invalid_return += 1
        else:
            values.append(observation.value)
    truncated = int(len(future) != HORIZON or (source_end is not None and bool(future) and future[-1] > source_end))
    reasons = []
    for flag, count in (("administrative_truncation", truncated),
                        ("missing_future_observation", missing_observation),
                        ("missing_future_return", missing_return),
                        ("invalid_future_return", invalid_return)):
        if count:
            reasons.append(flag)
    result = {k: "" for k in NULL_OUTCOME_FIELDS}
    end = future[-1] if len(future) == HORIZON else ""
    result.update(outcome_end=end, observed_outcome_end="", outcome_valid_candidate=0,
                  outcome_certified_total_return=0, supervised_candidate_mask=0,
                  supervised_certified_mask=0, future_observations_present=len(future) - missing_observation,
                  future_valid_returns=len(values), missing_future_observation_count=missing_observation,
                  missing_future_return_count=missing_return, invalid_future_return_count=invalid_return,
                  administrative_truncation=truncated, arithmetic_invalid=0,
                  outcome_status="unknown", unknown_reason="|".join(reasons))
    if reasons:
        return result
    try:
        value = compound_five(values)
    except ValueError:
        result.update(arithmetic_invalid=1, unknown_reason="arithmetic_invalid")
        return result
    result.update(forward_5d_return=str(value), **labels_from_return(value),
                  observed_outcome_end=end, outcome_valid_candidate=1, supervised_candidate_mask=1,
                  outcome_status="complete_raw_return_candidate")
    return result


def coverage_bounds(population, known_positive, unknown):
    """Bounds for the UNVERIFIED candidate denominator, never a market census."""
    if not all(isinstance(v, int) and not isinstance(v, bool) for v in (population, known_positive, unknown)):
        raise ValueError("Coverage counts must be integers")
    if min(population, known_positive, unknown) < 0 or known_positive + unknown > population:
        raise ValueError("Inconsistent coverage counts")
    known = population - unknown
    return {"population": population, "known_candidate_outcomes": known, "unknown": unknown,
            "known_positive": known_positive,
            "lower": known_positive / population if population else None,
            "upper": (known_positive + unknown) / population if population else None,
            "observed_positive_fraction": known_positive / known if known else None}


def target_identity(source_sha256, calendar_sha256, research_start, research_end, history_sessions):
    payload = {"target_contract": TARGET_CONTRACT, "returns_sha256": source_sha256,
               "calendar_sha256": calendar_sha256, "research_start": canonical_day(research_start),
               "research_end": canonical_day(research_end), "history_sessions": history_sessions}
    if not isinstance(history_sessions, int) or history_sessions < 1:
        raise ValueError("History length must be positive")
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return digest, payload


def stable_index_id(target_id, stock, day):
    source_key(stock, day)
    if not re.fullmatch(r"[0-9a-f]{64}", target_id):
        raise ValueError("Target identity must be a SHA256 digest")
    return hashlib.sha256((TARGET_VERSION + "\0" + target_id + "\0" + stock + "\0" + day).encode()).hexdigest()


def approved_folds():
    folds = []
    for year in range(2021, 2025):
        previous = year - 1
        folds.append({"name": str(year) if year < 2024 else "2024-partial",
                      "train_end": f"{year - 2}-12-31",
                      "selection_start": f"{previous}-01-01", "selection_end": f"{previous}-08-31",
                      "calibration_start": f"{previous}-09-01", "calibration_end": f"{previous}-12-31",
                      "test_start": f"{year}-01-01",
                      "test_end": f"{year}-12-31" if year < 2024 else "2024-07-24"})
    return folds
