"""Final-mixture four-bin temperature calibration and validation-only thresholds."""
from __future__ import annotations

from datetime import date
import numpy as np
from scipy.optimize import minimize_scalar

PROBABILITY_COLUMNS = ("p_loss", "p_loss_5pct", "p_loss_10pct")
BIN_COLUMNS = tuple(f"prob_bin_{i}" for i in range(4))


def normalized_probabilities(probability):
    p = np.asarray(probability, dtype=np.float64)
    if p.ndim != 2 or p.shape[1] != 4 or not np.isfinite(p).all() or (p < 0).any() or (p > 1).any():
        raise ValueError("Expected finite actual four-bin probabilities, not arbitrary scores")
    if not np.allclose(p.sum(1), 1., rtol=0, atol=1e-6):
        raise ValueError("Four-bin probabilities must sum to one")
    return p


def temperature_scale(probability, temperature=1.):
    p = normalized_probabilities(probability)
    if not np.isfinite(temperature) or temperature <= 0:
        raise ValueError("Temperature must be finite and positive")
    if temperature == 1.:
        return p.copy()
    # Preserve exactly zero bins; do not invent probabilities from unknown scores.
    with np.errstate(divide="ignore"):
        z = np.log(p) / temperature
    z -= z.max(1, keepdims=True)
    result = np.exp(z)
    return result / result.sum(1, keepdims=True)


def cumulative_probabilities(probability):
    p = normalized_probabilities(probability)
    return dict(zip(PROBABILITY_COLUMNS, (1 - p[:, 0], p[:, 2:].sum(1), p[:, 3])))


def validation_partition(days, validation_year):
    """Jan-Aug tune, Sep-Dec cal; no test-year threshold tuning."""
    roles = []
    for value in days:
        day = date.fromisoformat(str(value))
        if day.year != int(validation_year):
            raise ValueError("Validation date is outside validation year")
        roles.append("selection" if day.month <= 8 else "calibration")
    return np.asarray(roles)


def _calibration_labels(labels, days, outcome_end, validation_year, test_start):
    labels = np.asarray(labels)
    if labels.ndim != 1 or not np.isin(labels, [0, 1, 2, 3]).all():
        raise ValueError("Calibration requires known four-bin labels")
    if len(labels) != len(days) or len(labels) != len(outcome_end) or not len(labels):
        raise ValueError("Empty or inconsistent calibration records")
    if not (validation_partition(days, validation_year) == "calibration").all():
        raise ValueError("Temperature and alert thresholds use Sep-Dec Vcal only")
    cutoff = date.fromisoformat(str(test_start)).toordinal()
    ends = np.asarray(outcome_end)
    starts = np.asarray([date.fromisoformat(str(d)).toordinal() for d in days])
    if not np.isfinite(ends).all() or (ends <= starts).any() or (ends >= cutoff).any():
        raise ValueError("Calibration outcomes must finish before test: actual outcome_end purge")
    return labels.astype(int)


def fit_temperature(probability, labels, days, outcome_end, validation_year, test_start):
    p = normalized_probabilities(probability)
    y = _calibration_labels(labels, days, outcome_end, validation_year, test_start)
    if len(p) != len(y):
        raise ValueError("Probability/label length mismatch")
    def nll(log_temperature):
        q = temperature_scale(p, np.exp(log_temperature))
        return float(-np.log(np.maximum(q[np.arange(len(y)), y], np.finfo(float).tiny)).mean())
    fit = minimize_scalar(nll, bounds=(-6., 6.), method="bounded")
    if not fit.success or not np.isfinite(fit.fun):
        raise ValueError("Temperature fitting did not converge")
    temperature = float(np.exp(fit.x))
    return {"temperature": temperature, "calibration_fitted": True,
            "calibration_quality_verified": False, "fit_scope": "final_mixture_four_bins",
            "split_role": "calibration", "validation_year": int(validation_year), "rows": len(y),
            "date_start": min(map(str, days)), "date_end": max(map(str, days)),
            "test_start": str(test_start), "outcome_end_purged": True,
            "raw_age_bound_applies_after_temperature": temperature == 1.,
            "method": "positive_shared_temperature_NLL", "log_temperature_search_bounds": [-6., 6.]}


def select_threshold(probability, labels, *, days, outcome_end, validation_year, test_start,
                     criterion="F2", minimum_recall=None):
    """Frozen Vcal criterion. F2 by default; optional predeclared recall tradeoff.

    A target validation recall is not an all-risk guarantee on unseen data.
    """
    p, y = np.asarray(probability, dtype=float), np.asarray(labels)
    if not np.isfinite(p).all() or ((p < 0) | (p > 1)).any() or not np.isin(y, [0, 1]).all():
        raise ValueError("Invalid probabilities or binary calibration targets")
    # Reuse date/purge checks with binary values (also valid bin ids).
    _calibration_labels(y, days, outcome_end, validation_year, test_start)
    if len(p) != len(y):
        raise ValueError("Threshold length mismatch")
    if criterion not in {"F2", "validation_tradeoff"}:
        raise ValueError("Threshold criterion must be explicitly validation-only")
    if criterion == "validation_tradeoff" and (minimum_recall is None or not 0 < minimum_recall <= 1):
        raise ValueError("Predeclare minimum validation recall")
    meta = {"criterion": criterion, "split_role": "calibration", "selection_rows": len(y),
            "minimum_validation_recall": minimum_recall, "no_all_risk_guarantee": True}
    if not y.sum():
        return {**meta, "threshold": None, "status": "undefined_no_calibration_positives"}
    best = None
    for threshold in np.unique(np.r_[0., p, 1.]):
        alert = p >= threshold
        tp, fp, fn = int((alert & (y == 1)).sum()), int((alert & (y == 0)).sum()), int((~alert & (y == 1)).sum())
        recall = tp / (tp + fn)
        f2 = 5 * tp / max(5 * tp + 4 * fn + fp, 1)
        if criterion == "validation_tradeoff" and recall < minimum_recall:
            continue
        score = (f2, float(threshold)) if criterion == "F2" else (-fp, float(threshold))
        if best is None or score > best[0]:
            best = (score, float(threshold), recall, f2)
    return {**meta, "threshold": best[1], "status": "selected_on_Vcal", "selection_recall": best[2],
            "selection_F2": best[3]}
