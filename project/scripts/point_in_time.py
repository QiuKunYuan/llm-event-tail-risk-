"""Strict calendar joins and replacement snapshots; stdlib audit helpers."""
from bisect import bisect_right
from datetime import date


def available_session(publication_date, sessions, has_close_timestamp=False):
    """Date-only publications become available on the next market session.

    A same-day join needs separately verified before-close timestamps.
    """
    d = str(publication_date)[:10]
    date.fromisoformat(d)
    i = bisect_right(sessions, d)
    if has_close_timestamp and d in sessions:
        return d
    return sessions[i] if i < len(sessions) else None


def snapshot_at(disclosures, asof):
    """Input groups must be verified COMPLETE Top10 lists, not individual edges.

    Each group: company, available_date, report_date, version, complete,
    holders [{id, share}]. Revisions keep their own observable availability.
    A later disclosure for an older report cannot replace a newer report.
    """
    state = {}
    for item in sorted(disclosures, key=lambda x: (x["available_date"], x["version"])):
        if item["available_date"] > asof:
            continue
        if not item["complete"]:
            raise ValueError("Incomplete shareholder group cannot be used as a replacement snapshot")
        old = state.get(item["company"])
        if old is None or item["report_date"] >= old["report_date"]:
            state[item["company"]] = item
    return {company: item["holders"] for company, item in state.items()}


def temporal_partition(origin, outcome_end, train_end, validation_start, validation_end, test_start, test_end):
    """Purging by actual outcome end eliminates overlap at both boundaries."""
    if origin <= train_end and outcome_end < validation_start:
        return "train"
    if validation_start <= origin <= validation_end and outcome_end < test_start:
        return "validation"
    if test_start <= origin <= test_end:
        return "test"
    return "purged"
