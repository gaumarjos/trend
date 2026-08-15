"""
Download the full available daily-close history for every contract (all
rolls, current and expired) of the instruments listed in instruments.csv.

Requires: pip install ib_async pandas
TWS/Gateway must be running with API access enabled (Configure > API >
Settings > Enable ActiveX and Socket Clients), listening on PORT below.
"""

import csv
import time
from collections import deque
from datetime import date
from pathlib import Path

import pandas as pd
from ib_async import IB, Contract, Future, util

HOST = '127.0.0.1'
PORT = 7497          # 7497 = TWS paper, 7496 = TWS live, 4002/4001 = Gateway
CLIENT_ID = 7

INSTRUMENTS_CSV = Path('instruments.csv')
DATA_DIR = Path('hist')

BAR_SIZE = '1 day'
WHAT_TO_SHOW = 'TRADES'
CHUNK = '1 Y'           # window pulled per request; loop walks further back each time
EMPTY_RETRIES = 1       # retries before treating an empty response as "no more history"

# IB's blanket includeExpired=True listing only returns a recent window of
# expired contracts, not full history -- a targeted lookup for one specific
# contract month reaches further back. Every month is probed for every
# product rather than guessing month cycles per product: discovery calls
# aren't pacing-limited like historical-data requests, so trying all 12 is
# cheap, and it avoids silently missing contracts if a cycle guess is wrong.
YEARS_BACK = 5  # how far back to probe; IB just returns nothing for months that never had a contract

# Exchanges keep a rolling window of N listed quarters/months per product,
# so a newly-listed far-dated contract shows up shortly before the current
# furthest-known one expires -- not on a fixed external calendar we'd have
# to hardcode (and get wrong; see MJY/MSF). Rerun full discovery for a
# symbol only when its furthest known contract is within this many days of
# expiring; otherwise just top up the contracts already in the manifest.
DISCOVERY_BUFFER_DAYS = 60

# IB's historical-data pacing limit: no more than 60 requests in any rolling
# 10-minute window, and don't hammer it faster than a request per second either.
MAX_REQUESTS_PER_WINDOW = 60
WINDOW_SECONDS = 600
MIN_REQUEST_INTERVAL = 1.0

_request_times = deque()


def throttle():
    """Block until issuing another request won't breach IB's pacing limit."""
    now = time.time()
    while _request_times and now - _request_times[0] > WINDOW_SECONDS:
        _request_times.popleft()

    if len(_request_times) >= MAX_REQUESTS_PER_WINDOW:
        wait = WINDOW_SECONDS - (now - _request_times[0]) + 0.5
        print(f'\n  ...pacing limit reached, waiting {wait:.0f}s')
        time.sleep(wait)
        now = time.time()
        while _request_times and now - _request_times[0] > WINDOW_SECONDS:
            _request_times.popleft()
    elif _request_times and now - _request_times[-1] < MIN_REQUEST_INTERVAL:
        time.sleep(MIN_REQUEST_INTERVAL - (now - _request_times[-1]))

    _request_times.append(time.time())


def load_instruments():
    """
    Each row in instruments.csv lists a micro and/or a regular (full-size)
    contract for the same product -- whichever are present get downloaded
    independently. Either column may be blank (e.g. a product with no
    micro-sized version).
    """
    with INSTRUMENTS_CSV.open() as f:
        reader = csv.DictReader(f, skipinitialspace=True)
        rows = [{k.strip(): v.strip() for k, v in row.items()} for row in reader]

    instruments = []
    for row in rows:
        exchange = row['Exchange']
        if row['Micro']:
            instruments.append({'symbol': row['Micro'], 'exchange': exchange})
        if row['Regular']:
            instruments.append({'symbol': row['Regular'], 'exchange': exchange})
    return instruments


def candidate_months():
    this_year = date.today().year
    return [
        f'{year}{month:02d}'
        for year in range(this_year - YEARS_BACK, this_year + 2)
        for month in range(1, 13)
    ]


def discover_contracts(ib, symbol, exchange):
    """
    Every contract (current + expired) IB still knows about for this symbol.
    Combines the broad includeExpired listing (cheap, catches current/recent
    contracts) with a targeted month-by-month probe (slower, but reaches
    further back -- the broad listing alone only surfaces roughly the last
    year of expired contracts).
    """
    found = {}

    template = Future(symbol=symbol, exchange=exchange, includeExpired=True)
    for d in ib.reqContractDetails(template):
        found[d.contract.conId] = d.contract

    months = candidate_months()
    print(f'  scanning {len(months)} candidate contract months...', end=' ', flush=True)
    for yyyymm in months:
        probe = Future(symbol=symbol, exchange=exchange,
                        lastTradeDateOrContractMonth=yyyymm, includeExpired=True)
        try:
            details = ib.reqContractDetails(probe)
        except Exception:
            details = []
        time.sleep(0.25)
        for d in details:
            found[d.contract.conId] = d.contract
    print(f'{len(found)} contracts found')

    return sorted(found.values(), key=lambda c: c.lastTradeDateOrContractMonth)


def needs_discovery(existing_manifest, symbol):
    """
    Whether `symbol` needs a full discovery sweep this run, vs. just topping
    up the contracts already known about it. True if we don't know about
    this symbol yet, or its furthest-dated known contract expires within
    DISCOVERY_BUFFER_DAYS -- see the constant's comment for why.
    """
    if existing_manifest is None:
        return True

    rows = existing_manifest[existing_manifest['instrument'] == symbol]
    if rows.empty:
        return True

    furthest_expiry = pd.to_datetime(rows['expiry'].astype(str), format='%Y%m%d', errors='coerce').max()
    if pd.isna(furthest_expiry):
        return True

    return (furthest_expiry.date() - date.today()).days <= DISCOVERY_BUFFER_DAYS


def contracts_from_manifest(ib, existing_manifest, symbol):
    """
    Rebuild fully-qualified Contract objects for `symbol` from what's
    already recorded in the manifest, without a discovery sweep -- each
    contract's conId alone is enough for IB to resolve it exactly.
    """
    rows = existing_manifest[existing_manifest['instrument'] == symbol]

    contracts = []
    for _, row in rows.iterrows():
        c = Contract(conId=int(row['conId']), exchange=row['exchange'], currency=row['currency'])
        qualified = ib.qualifyContracts(c)
        if qualified:
            contracts.append(qualified[0])

    return sorted(contracts, key=lambda c: c.lastTradeDateOrContractMonth)


def req_bars_with_retry(ib, contract, end):
    for attempt in range(EMPTY_RETRIES + 1):
        throttle()
        bars = ib.reqHistoricalData(
            contract,
            endDateTime=end,
            durationStr=CHUNK,
            barSizeSetting=BAR_SIZE,
            whatToShow=WHAT_TO_SHOW,
            useRTH=False,
            formatDate=1,
        )
        if bars:
            return bars
        time.sleep(2.0 * (attempt + 1))
    return []


def fetch_full_history(ib, contract):
    """Walk backwards in CHUNK-sized windows until IB has nothing earlier left."""
    all_bars = []
    end = ''  # '' = now / contract's last activity
    seen_earliest = None

    while True:
        bars = req_bars_with_retry(ib, contract, end)
        if not bars:
            break

        earliest = bars[0].date
        if seen_earliest is not None and earliest >= seen_earliest:
            break  # no progress, stop

        all_bars = bars + all_bars
        seen_earliest = earliest
        end = earliest.strftime('%Y%m%d %H:%M:%S') if hasattr(earliest, 'strftime') else str(earliest)

        if len(bars) < 2:
            break  # essentially nothing new, we've hit the start of history

    if not all_bars:
        return None

    df = util.df(all_bars)
    df = df.drop_duplicates(subset='date').sort_values('date').reset_index(drop=True)
    return df


def fetch_incremental_history(ib, contract, since_date):
    """
    Bars from after `since_date` up to now, for topping up a file that's
    already on disk. A single request comfortably covers any realistic gap
    between runs, so unlike fetch_full_history this doesn't walk backward.
    since_date=None (an empty cached file -- listed but hadn't traded yet
    last time) returns everything found.
    """
    bars = req_bars_with_retry(ib, contract, end='')
    if not bars:
        return None

    df = util.df(bars)
    if since_date is not None:
        df = df[df['date'] > since_date]
    df = df.drop_duplicates(subset='date').sort_values('date').reset_index(drop=True)
    return df


def main():
    ib = IB()
    ib.connect(HOST, PORT, clientId=CLIENT_ID, readonly=True)

    instruments = load_instruments()
    manifest_rows = []

    manifest_path = DATA_DIR / 'manifest.csv'
    existing_manifest = pd.read_csv(manifest_path) if manifest_path.exists() else None

    for row in instruments:
        symbol = row['symbol']
        exchange = row['exchange']
        print(f'\n=== {symbol} ({exchange}) ===')

        out_dir = DATA_DIR / symbol
        out_dir.mkdir(parents=True, exist_ok=True)

        try:
            if needs_discovery(existing_manifest, symbol):
                contracts = discover_contracts(ib, symbol, exchange)
            else:
                print('  known contracts still have runway, skipping discovery')
                contracts = contracts_from_manifest(ib, existing_manifest, symbol)
        except Exception as e:
            print(f'  contract discovery failed: {e}')
            continue

        if not contracts:
            print('  no contracts found')
            continue

        for contract in contracts:
            local = contract.localSymbol or f'{symbol}{contract.lastTradeDateOrContractMonth}'
            out_file = out_dir / f'{local}.csv'

            if out_file.exists():
                df = pd.read_csv(out_file, parse_dates=['date'])

                expiry_date = pd.to_datetime(str(contract.lastTradeDateOrContractMonth), format='%Y%m%d', errors='coerce')
                still_active = pd.isna(expiry_date) or expiry_date.date() >= date.today()
                last_date = df['date'].max() if not df.empty else None
                needs_topup = still_active and (last_date is None or last_date.date() < date.today())

                if needs_topup:
                    print(f'  {local}  (expiry {contract.lastTradeDateOrContractMonth}) '
                          f'topping up since {last_date}...', end=' ', flush=True)

                    new_bars = fetch_incremental_history(ib, contract, since_date=last_date)
                    if new_bars is not None and not new_bars.empty:
                        df = pd.concat([df, new_bars]).drop_duplicates(subset='date').sort_values('date').reset_index(drop=True)
                        df.to_csv(out_file, index=False)
                        print(f'+{len(new_bars)} bars, now {len(df)} total, {df["date"].min()} -> {df["date"].max()}')
                    else:
                        print('no new bars')
                elif df.empty:
                    print(f'  {local}  (expiry {contract.lastTradeDateOrContractMonth}) '
                          f'no trade history, contract now expired, nothing more to fetch')
                else:
                    print(f'  {local}  (expiry {contract.lastTradeDateOrContractMonth}) '
                          f'up to date, {len(df)} bars, skipping')
            else:
                print(f'  {local}  (expiry {contract.lastTradeDateOrContractMonth}) ...', end=' ', flush=True)

                df = fetch_full_history(ib, contract)
                if df is None:
                    # Listed (e.g. a far-dated back month) but never traded -- still
                    # record it, as an empty file, so it isn't silently missing from
                    # the manifest and isn't re-probed on every future run.
                    df = pd.DataFrame(columns=['date', 'open', 'high', 'low', 'close', 'volume', 'average', 'barCount'])

                df.to_csv(out_file, index=False)

                if df.empty:
                    print('no trade history yet')
                else:
                    print(f'{len(df)} bars, {df["date"].min()} -> {df["date"].max()}')

            manifest_rows.append({
                'instrument': symbol,
                'localSymbol': local,
                'conId': contract.conId,
                'expiry': contract.lastTradeDateOrContractMonth,
                'exchange': contract.exchange,
                'multiplier': contract.multiplier,
                'currency': contract.currency,
                'file': str(out_file.relative_to(DATA_DIR)),
                'firstDate': str(df['date'].min()) if not df.empty else '',
                'lastDate': str(df['date'].max()) if not df.empty else '',
            })

    manifest_df = pd.DataFrame(manifest_rows)
    manifest_df.to_csv(DATA_DIR / 'manifest.csv', index=False)
    print(f'\nDone. Manifest written to {DATA_DIR / "manifest.csv"}')

    ib.disconnect()


if __name__ == '__main__':
    main()
