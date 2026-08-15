import pandas as pd
import loader

HORIZONS = (21, 63, 252)  # trading days: ~1 month, ~1 quarter, ~1 year

def signal_sign(instrument, date, horizon, method='back_adjusted'):
    """
    Sign-of-return trend signal for `instrument` as of `date`, over a single
    `horizon` (in trading days): +1 if price is higher than `horizon`
    trading days ago, -1 if lower, 0 if unchanged. None if `date` wasn't a
    trading day or there isn't enough history for that horizon.

    `method` selects which merge_contracts_in_timeseries price-series
    construction to compute the sign from (manage_overlap value -- see that
    function's docstring); it matters here because the sign of a return
    spanning a roll date depends on whether the roll jump got adjusted away.
    """
    date = pd.Timestamp(date)
    series = loader.merge_contracts_in_timeseries(instrument, manage_overlap=method)

    if date not in series.index:
        return None

    pos = series.index.get_loc(date)
    past_pos = pos - horizon
    if past_pos < 0:
        return None

    current = series.iloc[pos]
    past = series.iloc[past_pos]
    return 1 if current > past else (-1 if current < past else 0)


SIGNAL_ALGORITHMS = {
    'sign': signal_sign,
}


def voting(instrument, date, horizons=HORIZONS, algorithm='sign', method='back_adjusted'):
    """
    Average vote across `horizons` for `instrument` as of `date`. For each
    horizon, calls the per-horizon signal function selected by `algorithm`
    (currently only 'sign' -> signal_sign; add new trend-detection
    algorithms to SIGNAL_ALGORITHMS and they become selectable the same
    way). `method` is forwarded to that function.

    Returns (votes, average) where votes is {horizon: vote}, containing
    only the horizons that had enough history to compute a signal -- so it
    can have fewer entries than `horizons`, down to none at all. Never
    raises for limited history: if no horizon could be computed, votes is
    {} and average is None, so callers always get a usable number.
    """
    assert algorithm in SIGNAL_ALGORITHMS, f'unknown algorithm: {algorithm!r}'
    signal_fn = SIGNAL_ALGORITHMS[algorithm]

    raw_votes = {h: signal_fn(instrument, date, h, method=method) for h in horizons}
    votes = {h: v for h, v in raw_votes.items() if v is not None}

    if not votes:
        return {}, None

    average = sum(votes.values()) / len(votes)
    return votes, average
