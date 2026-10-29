"""Coverage-only suitability checks. No strategy returns are used for data selection."""

import pandas as pd


def coverage(frame, experiment):
    original = experiment.requirement
    requested = sorted(set(original.symbols) or set(frame.security_id))
    security_months = {s: set(g.date) for s, g in frame.groupby('security_id')}
    summary = [{'security_id': s, 'first': str(min(d).date()), 'last': str(max(d).date()),
                'months': len(d)} for s, d in security_months.items()]
    warmup = experiment.formation_months + experiment.holding_months + experiment.skip_months - 1
    latest = min(pd.Timestamp(original.end), frame.date.max())
    windows = [(pd.Timestamp(original.start), latest, 'requested_period')]
    for years in [10, 5]:
        end = frame.date.max()
        start = (end - pd.DateOffset(years=years) + pd.offsets.MonthBegin()).normalize()
        windows.append((start, end, f'available_{years}_year_period'))
    options = []
    for start, end, label in windows:
        if (end.to_period('M') - start.to_period('M')).n < 2:
            continue
        first = start.to_period('M').to_timestamp('M') - pd.offsets.MonthEnd(warmup)
        months = set(pd.date_range(first, end.to_period('M').to_timestamp('M'), freq='ME'))
        eligible = sorted(s for s in requested if months <= security_months.get(s, set()))
        minimum = experiment.quantiles if experiment.method == 'momentum_sort' else 1
        if len(eligible) < minimum:
            continue
        if experiment.method == 'return_series' and (len(original.symbols) != 1 or len(eligible) != 1):
            continue
        requirement = original.model_copy(update={'symbols': eligible, 'start': start.date(), 'end': end.date()})
        options.append({'name': label, 'requirement': requirement.model_dump(mode='json'),
                        'eligible_securities': len(eligible), 'warmup_months': warmup,
                        'selection_basis': 'complete date coverage only; no return-performance selection'})
    return {'rows': len(frame), 'securities': frame.security_id.nunique(),
            'start': str(frame.date.min().date()), 'end': str(frame.date.max().date()),
            'frequency': 'monthly', 'security_coverage': summary,
            'missing_requested_symbols': sorted(set(original.symbols) - set(security_months)),
            'supported_options': options, 'historical_membership': 'not established by price observations',
            'missing_held_returns': 'complete monthly windows required; execution checks held positions'}


def validate_selection(frame, experiment):
    quality = coverage(frame, experiment)
    matching = [o for o in quality['supported_options'] if o['name'] == 'requested_period'
                and set(o['requirement']['symbols']) == set(experiment.requirement.symbols)
                and o['requirement']['start'] == str(experiment.requirement.start)
                and o['requirement']['end'] == str(experiment.requirement.end)]
    if not matching:
        raise ValueError('Selected experiment does not have complete security-month coverage including lookback')
