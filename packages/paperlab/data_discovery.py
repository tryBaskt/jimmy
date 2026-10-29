"""Catalog-first discovery, provider universe resolution and bounded alternatives."""

from datetime import date
import pandas as pd
from paperlab.catalog import Catalog
from paperlab.universe import UniverseResolver, BigdataUniverseConnector, YahooUniverseConnector


class DataDiscovery:
    def __init__(self, store, registry, notify):
        self.store, self.registry, self.notify = store, registry, notify

    def discover(self, experiment, project):
        requirement = experiment.requirement
        connectors = getattr(self.registry, 'connectors', {})
        scopes = {c.scope for c in connectors.values() if hasattr(c, 'scope')}
        found = Catalog(self.store).find(requirement, scopes)
        if found:
            self.notify('catalog_candidates', 'Compatible catalog datasets discovered before provider search',
                        {'candidate_ids': [c.id for c in found]})
            return found, []
        direct, errors = self.registry.discover(requirement, project)
        found.extend(direct)
        if (requirement.kind not in {'prices', 'returns'} or requirement.symbols
                or experiment.method != 'momentum_sort' or not connectors):
            return found, errors
        bigdata = connectors.get('bigdata')
        providers = []
        if bigdata and bigdata.api_key:
            providers.append(BigdataUniverseConnector(bigdata.api_key, bigdata.scope, bigdata.mcp))
        else:
            errors.append({'connector': 'bigdata_universe', 'code': 'credentials_not_configured'})
        providers.append(YahooUniverseConnector())
        errors.append({'connector': 'bigdata', 'code': 'mcp_daily_prices_not_exposed',
                       'message': 'Use Yahoo price histories; no Bigdata REST fallback is enabled'})
        options, issues = UniverseResolver(self.store, providers, self.notify).resolve(requirement)
        errors.extend(issues)
        for option in options:
            symbols = sorted(m['symbol'] for m in option['members'])
            resolved = requirement.model_copy(update={'symbols': symbols})
            end = (pd.Timestamp(date.today()).to_period('M').to_timestamp('M') - pd.offsets.MonthEnd()).date()
            start = (pd.Timestamp(end) - pd.DateOffset(years=10) + pd.offsets.MonthBegin()).date()
            later = resolved.model_copy(update={'start': start, 'end': end})
            for request, label in [(resolved, 'original_period_substitute_universe'),
                                   (later, 'later_period_substitute_universe')]:
                choices = []
                yahoo = connectors.get('yfinance')
                if yahoo:
                    for candidate in yahoo.discover(request, project):
                        candidate.caveats.extend(option['caveats'])
                        candidate.metadata.update(universe=option['provenance'], return_basis='total_return')
                        choices.append(candidate)
                for candidate in choices:
                    candidate.metadata.update(proposal=label, suggested_requirement=request.model_dump(mode='json'))
                    found.append(candidate)
            # Prefer an identity-rich Bigdata universe; Yahoo is a genuine fallback if that source fails.
            if any(c.kind == 'stock_returns' for c in found):
                break
        return list({c.id: c for c in found}.values()), errors
