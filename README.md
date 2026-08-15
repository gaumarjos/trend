# TREND FOLLOWING

Just another strategy backtester, for now.


## Inspired by
- https://www.thetabruv.com/en/docs/esecuzione/bot-cta/
- https://www.thetabruv.com/en/docs/risorse/


## Repo structure

| File | Scope | Run with |
| :--- | :---- | :------- |
| `instruments.csv`         | Hardcoded list of future contract the code is dealing with. | - |
| `download.py`             | To be run on any new day to update the dataset in `hist/`. Occasionally, it get new contracts. | `python download.py` |
| `loader.py`               | Load data from `hist/` and make it available. | - |
| `tsmom_signals.py`        | Detects Time Series MOMentum signals | - |
| `backtester.py`           | To run experiments | `python backtester.py` |



## How to use it

There is no difference between backtesting and production. Running the script with today's date assumes it's operating live, while running it with a date in the past
assumes backtesting.

1. Run `backtester.py`