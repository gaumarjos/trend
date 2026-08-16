"""
One-off repair: reconcile hist/manifest.csv against what's actually on disk
under hist/<SYMBOL>/*.csv.

Needed because download.py used to write the manifest only once, at the end
of a full run across every instrument -- a run that got interrupted partway
through silently dropped the manifest rows for every symbol, including ones
that had already finished downloading correctly. That's now fixed to write
incrementally, but this repairs the gap it already left behind.

Purely local, no TWS/IB connection: instrument/localSymbol/file/firstDate/
lastDate come directly from the directory structure and each file's own
contents. 'expiry' is approximated as the file's own last recorded date --
not the contract's exact calendar expiry, but nothing downstream needs the
exact day, only correct ordering between contracts of the same instrument,
which the last date on file preserves just as well. 'exchange' comes from
instruments.csv. conId/multiplier/currency are left blank -- nothing
currently reads them, and there's no reliable local way to recover them; a
future download.py discovery run for that symbol will fill them in properly.
"""

import pandas as pd

from download import DATA_DIR, load_instruments


def main():
    manifest_path = DATA_DIR / 'manifest.csv'
    existing_manifest = pd.read_csv(manifest_path) if manifest_path.exists() else pd.DataFrame()
    manifest_rows = existing_manifest.to_dict('records')
    known = {(r['instrument'], r['localSymbol']) for r in manifest_rows}

    symbol_exchange = {i['symbol']: i['exchange'] for i in load_instruments()}

    symbol_dirs = sorted(d for d in DATA_DIR.iterdir() if d.is_dir())

    for symbol_dir in symbol_dirs:
        symbol = symbol_dir.name
        local_files = sorted(symbol_dir.glob('*.csv'))

        missing = [f for f in local_files if (symbol, f.stem) not in known]
        if not missing:
            continue

        print(f'{symbol}: {len(missing)} file(s) on disk missing from manifest')
        exchange = symbol_exchange.get(symbol, '')
        if not exchange:
            print(f'  {symbol} is not in instruments.csv, exchange will be left blank')

        for f in missing:
            df = pd.read_csv(f, parse_dates=['date'])
            last_date = df['date'].max() if not df.empty else None

            manifest_rows.append({
                'instrument': symbol,
                'localSymbol': f.stem,
                'conId': '',
                'expiry': last_date.strftime('%Y%m%d') if last_date is not None else '',
                'exchange': exchange,
                'multiplier': '',
                'currency': '',
                'file': str(f.relative_to(DATA_DIR)),
                'firstDate': str(df['date'].min()) if not df.empty else '',
                'lastDate': str(df['date'].max()) if not df.empty else '',
            })
            print(f'  {f.name}: recovered (approx expiry {last_date.date() if last_date is not None else "unknown"})')

    pd.DataFrame(manifest_rows).to_csv(manifest_path, index=False)
    print(f'\nDone. Manifest updated: {manifest_path}')


if __name__ == '__main__':
    main()
