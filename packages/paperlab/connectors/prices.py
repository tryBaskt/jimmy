"""Price normalization without filling gaps or claiming undocumented adjustments."""

import pandas as pd


def monthly_returns(series, security_id):
    series = series.sort_index()
    if series.index.has_duplicates or (series <= 0).any():
        raise ValueError('Duplicate dates or nonpositive prices')
    monthly = series.resample('ME').last()
    # Reindex first, so missing months never become multi-month returns.
    monthly = monthly.reindex(pd.date_range(monthly.index.min(), monthly.index.max(), freq='ME'))
    returns = monthly.pct_change(fill_method=None).dropna()
    return pd.DataFrame({'date': returns.index.strftime('%Y-%m-%d'),
                         'security_id': security_id, 'return': returns.to_numpy()})


def encode_frames(frames):
    frames = [f for f in frames if not f.empty]
    if not frames:
        raise ValueError('No usable monthly price observations in the requested period')
    frame = pd.concat(frames).sort_values(['date', 'security_id'])
    return frame.to_csv(index=False, float_format='%.12g').encode(), frame
