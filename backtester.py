import loader
import tsmom_signals as tsmom
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd



def sweep(instrument, start_date, end_date, plot=False):
    """
    Runs tsmom.voting(instrument, date) for every calendar date between
    start_date and end_date (inclusive). Dates with no usable data --
    weekends, before the instrument existed, not enough history yet for
    any horizon -- aren't special-cased here: voting() already returns
    ({}, None) for those rather than raising, so this just collects
    whatever comes back.
    """
    rows = []
    for d in pd.date_range(start_date, end_date, freq='D'):
        votes, average = tsmom.voting(instrument, d)
        rows.append({'date': d, 'votes': votes, 'average': average})

    df = pd.DataFrame(rows, columns=['date', 'votes', 'average'])

    if plot:
        series = loader.merge_contracts_in_timeseries(instrument)
        series = series[(series.index >= df['date'].min()) & (series.index <= df['date'].max())]

        # No external market-calendar needed: the price series only has a
        # bar on days the market actually traded, so its index already is
        # the exact trading calendar. Plotting against each day's position
        # in it, rather than its real date, drops weekends/holidays from
        # the axis entirely instead of just avoiding gaps in the line.
        trading_days = series.index

        plot_df = df[df['votes'].apply(bool)].copy()
        plot_df['x'] = plot_df['date'].apply(trading_days.get_loc)

        horizons = sorted({h for v in plot_df['votes'] for h in v.keys()})
        grays = np.linspace(0.75, 0.15, len(horizons)) if horizons else []

        fig, (ax_price, ax_vote) = plt.subplots(2, 1, sharex=True, gridspec_kw={'height_ratios': [2, 1]})

        ax_price.plot(range(len(trading_days)), series.values, color='blue', label=instrument)
        ax_price.set_ylabel('price')
        ax_price.set_title(f'{instrument}: price vs. trend votes')
        ax_price.legend(loc='upper left')
        ax_price.grid()

        for h, gray in zip(horizons, grays):
            ax_vote.plot(plot_df['x'], plot_df['votes'].apply(lambda v: v.get(h)),
                         linestyle='None', marker='.', color=str(gray), label=f'{h}d vote')
        ax_vote.plot(plot_df['x'], plot_df['average'], color='red', label='average')
        ax_vote.set_ylabel('vote')
        ax_vote.set_ylim(-1.1, 1.1)
        ax_vote.set_xlabel('date')
        ax_vote.legend(loc='upper left')
        ax_vote.grid()

        # Trading-day-index x-axis needs its own date labels, since it's no
        # longer a real datetime axis.
        tick_positions = list(range(0, len(trading_days), max(1, len(trading_days) // 8)))
        ax_vote.set_xticks(tick_positions)
        ax_vote.set_xticklabels([trading_days[i].strftime('%Y-%m-%d') for i in tick_positions],
                                 rotation=45, ha='right')

        plt.show()

    return df
    


if __name__ == '__main__':

    instrument = 'MGC'
    date = '2026-08-14'

    '''
    df = loader.contracts_on(instrument, date)
    print(f'{instrument} contracts tradeable on {date}:')
    print(df)
    '''


    '''
    fig, axes = plt.subplots(2, 1, sharex=True)
    for ax, manage_roll in zip(axes, ('expiry', 'volume')):
        for manage_overlap in ('front_m', 'back_adjusted', 'ratio_adjusted'):
            ts = loader.merge_contracts_in_timeseries(instrument, manage_overlap=manage_overlap, manage_roll=manage_roll)
            print(tsmom.voting(instrument, '2026-08-14'))
            ax.plot(ts, label=manage_overlap)

        ax.set(title=f'manage_roll={manage_roll}', ylabel='close')
        ax.legend()
        ax.grid()

    axes[-1].set(xlabel='date')
    plt.show()
    '''


    instrument = 'MZS'
    #_, vote = tsmom.voting(instrument, date)
    #print(vote)
    sweep(instrument, '2024-01-01', '2026-08-14', plot=True)
    #sweep(instrument, '2024-01-01', '2026-08-14', plot=True)
