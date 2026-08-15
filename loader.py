"""
Query which contracts of a given instrument were tradeable on a given date,
and at what price -- i.e. "what could I have bought that day".

Reads the CSVs and manifest.csv written by download.py.
"""

from functools import lru_cache
from pathlib import Path

import pandas as pd

DATA_DIR = Path('hist')


@lru_cache(maxsize=None)
def _load_manifest():
    return pd.read_csv(DATA_DIR / 'manifest.csv')


@lru_cache(maxsize=None)
def _load_contract(file):
    df = pd.read_csv(DATA_DIR / file, parse_dates=['date'])
    return df.set_index('date').sort_index()


def _contracts_on_live(instrument):
    """Live IBKR query for what's tradeable right now."""
    raise NotImplementedError('live contract querying via IBKR is not implemented yet')


def contracts_on(instrument, date):
    """
    Every contract of `instrument` that actually traded on `date`, indexed
    by localSymbol and sorted front-month first (soonest expiry first).
    Columns include expiry, open, high, low, close, volume.

    Returns an empty DataFrame if nothing traded that day (weekend/holiday,
    or the date is before/after the instrument existed).

    `date` being today routes to a live IBKR query (see _contracts_on_live)
    instead of the local historical store -- no separate live/backtest
    switch needed, since the date being asked about already says which one
    is meant.
    """
    date = pd.Timestamp(date)

    if date.date() == pd.Timestamp.today().date():
        return _contracts_on_live(instrument)

    manifest = _load_manifest()
    rows = manifest[manifest['instrument'] == instrument]

    found = []
    for _, row in rows.iterrows():
        df = _load_contract(row['file'])
        if date in df.index:
            bar = df.loc[date].to_dict()
            bar['localSymbol'] = row['localSymbol']
            bar['expiry'] = pd.to_datetime(str(row['expiry']), format='%Y%m%d')
            found.append(bar)

    if not found:
        return pd.DataFrame()

    return pd.DataFrame(found).set_index('localSymbol').sort_values('expiry')


@lru_cache(maxsize=None)
def merge_contracts_in_timeseries(instrument, manage_overlap='back_adjusted', manage_roll='expiry', roll_offset_days=5):
    """
    Chaining of multiple contracts into a single timeseries that can be evaluated to identify trends.

    To do so, it must be decided WHEN the switch happens and how PRICE JUMPS are treated.

    About when, options are:
    - based on which contract is closer to 'expiry' - 'roll_offset_days' (*)
    - based on whichever contract has the highest 'volume' on that day

    About price corrections, options are:
    - front_m:           raw splice, no adjustment, resulting in a jump at every roll equal to the price gap between the
                         outgoing and incoming contract.
    - back_adjusted (*): the resulting timeseries has no jumps at each roll; most trend systems size positions and stops
                         in point terms, which this preserves as real across the whole history.
    - ratio_adjusted:    same idea as back_adjusted, but scales each historical segment by the price ratio
                         instead of shifting by a fixed amount, preserving percentage returns instead of
                         absolute point differences. It is useful when comparing instruments with very different
                         price scales.

    TODO: depending on the method using for signal identification later on, the impact of these methods can end up being irrelevant
    """
    assert manage_roll in ('expiry', 'volume'), f'unknown manage_roll: {manage_roll!r}'
    assert manage_overlap in ('front_m', 'back_adjusted', 'ratio_adjusted'), f'unknown manage_overlap: {manage_overlap!r}'
    assert roll_offset_days >= 0, f'roll_offset_days must be >= 0, got {roll_offset_days!r}'

    manifest = _load_manifest()
    rows = manifest[manifest['instrument'] == instrument]

    records = []
    for _, row in rows.iterrows():
        df = _load_contract(row['file'])
        if df.empty:
            continue
        expiry = pd.to_datetime(str(row['expiry']), format='%Y%m%d', errors='coerce')
        for dt, bar in df.iterrows():
            records.append((dt, expiry, bar['volume'], bar['close']))

    if not records:
        return pd.Series(dtype=float)

    bars_raw = pd.DataFrame(records, columns=['date', 'expiry', 'volume', 'close'])
    # print(bars_raw)

    # Pick which contract counts as "front" (active) on each date -- this is
    # the roll-timing decision, independent of what happens to price levels
    # at the roll (handled below by manage_overlap).
    if manage_roll == 'expiry':
        # Nearest-to-expiry contract that traded that day, excluding each
        # contract's own last roll_offset_days trading days so the roll
        # happens before expiry, not at it -- avoids riding into a
        # contract's most illiquid final stretch. Falls back to the plain
        # nearest-expiry pick (ignoring the offset) on any date where every
        # trading contract was within its own offset window -- e.g. right
        # at the tail of currently available data, before a later contract
        # has enough history -- so those dates aren't silently dropped.
        by_expiry = bars_raw.sort_values(['expiry', 'date'])
        days_to_last = by_expiry.groupby('expiry').cumcount(ascending=False)
        eligible = by_expiry[days_to_last >= roll_offset_days]

        front = eligible.sort_values(['date', 'expiry']).groupby('date').first()
        missing = bars_raw[~bars_raw['date'].isin(front.index)]
        if not missing.empty:
            fallback = missing.sort_values(['date', 'expiry']).groupby('date').first()
            front = pd.concat([front, fallback])
        front = front.sort_index()
    else:
        # Whichever contract had the most volume that day.
        front = bars_raw.sort_values(['date', 'volume'], ascending=[True, False]).groupby('date').first().sort_index()

    if manage_overlap == 'front_m':
        # Splice as-is: raw jump at every roll.
        return front['close'].sort_index()

    # back_adjusted (Panama) / ratio_adjusted (proportional): walk
    # roll transitions backward through history, shifting (back_adjusted) or
    # scaling (ratio_adjusted) each older segment so the series has no jumps.
    # The current (most recent) segment is left untouched. This makes old
    # absolute price levels fictional (back_adjusted can even go negative
    # under long-term contango) -- only day-to-day changes are preserved as
    # real.
    segment = (front['expiry'] != front['expiry'].shift()).cumsum()
    adjusted = front['close'].copy()
    running = 0.0 if manage_overlap == 'back_adjusted' else 1.0

    for seg_id in sorted(segment.unique(), reverse=True)[1:]:
        older_mask = segment == seg_id
        newer_mask = segment == seg_id + 1

        roll_date = front.index[older_mask][-1]  # outgoing contract's last day as front
        outgoing_expiry = front.loc[roll_date, 'expiry']
        incoming_expiry = front.loc[front.index[newer_mask][0], 'expiry']

        outgoing_price = bars_raw.loc[(bars_raw['date'] == roll_date) & (bars_raw['expiry'] == outgoing_expiry), 'close']
        incoming_price = bars_raw.loc[(bars_raw['date'] == roll_date) & (bars_raw['expiry'] == incoming_expiry), 'close']

        if outgoing_price.empty or incoming_price.empty:
            # The two contracts didn't both trade on the roll date, so
            # there's no clean gap/ratio to compute -- this one roll is
            # left as a raw (unadjusted) jump rather than guessing.
            continue

        if manage_overlap == 'back_adjusted':
            running += incoming_price.iloc[0] - outgoing_price.iloc[0]
            adjusted.loc[older_mask] += running
        elif manage_overlap == 'ratio_adjusted':
            if outgoing_price.iloc[0] == 0:
                continue  # ratio undefined
            running *= incoming_price.iloc[0] / outgoing_price.iloc[0]
            adjusted.loc[older_mask] *= running

    return adjusted.sort_index()
