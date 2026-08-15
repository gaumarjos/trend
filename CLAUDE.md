# Working conventions for this project

## Don't silently drop or reshape data

When code has to decide what counts as "no data" / "doesn't exist" / "not
worth keeping" and the natural implementation would quietly exclude those
cases from output, call it out and confirm the intended behavior instead of
just implementing the exclusion.

Precedent: `download.py` (then named `download_history.py`) originally dropped any futures contract
with zero trade history from `manifest.csv` when it turned out empty from
`fetch_full_history`. This looked like a downloader bug but was actually a
silent policy choice (only record contracts that have traded), and it hid
real, currently-tradeable contracts (e.g. M2K's far-dated back months
H7/M7/U7) from `loader.contracts_on()` (then named `data_loader.contracts_on()`) without any indication they'd
been excluded.

Before writing code that filters, drops, truncates, or otherwise decides
some data isn't worth keeping: surface the tradeoff and the concrete case
it affects, rather than picking a default and moving on.
