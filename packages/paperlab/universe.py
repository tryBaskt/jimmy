"""Provider-backed universe snapshots; current listings never imply historical membership."""

from datetime import date
import json
import re
from paperlab.catalog import Catalog
from paperlab.connectors import Candidate
from paperlab.models import UniverseDefinition
from paperlab.storage import digest, encode
from paperlab.connectors.bigdata_mcp import BigdataMCP

MAX_MEMBERS = 100
MAX_PAGES = 4


def definition(requirement):
    if requirement.universe:
        return requirement.universe
    # Backward compatibility for already frozen plans: use explicit exchange names only.
    text = requirement.description.upper()
    exchanges = [e for e in ['NYSE', 'AMEX', 'NASDAQ'] if re.search(r'\b' + e + r'\b', text)]
    if exchanges:
        return UniverseDefinition(description=requirement.description, exchanges=exchanges)
    return None


class BigdataUniverseConnector:
    name = 'bigdata_universe'

    def __init__(self, api_key, scope, mcp=None):
        self.api_key, self.scope = api_key, scope
        self.mcp = mcp or BigdataMCP(api_key)

    def fetch(self, candidate):
        aliases = {'NYSE': 'XNYS', 'NASDAQ': 'XNAS', 'AMEX': 'ARCX'}
        exchanges = [aliases.get(e, e) for e in candidate.request['exchanges']]
        cursor, rows, truncated, as_of = None, [], False, None
        for _ in range(MAX_PAGES):
            body = {'exchange': exchanges, 'is_etf': False, 'is_fund': False, 'limit': 100}
            if cursor:
                body['cursor'] = cursor
            payload = self.mcp.call('bigdata_screen_companies', body)
            rows.extend(payload.get('companies', []))
            as_of = payload.get('as_of') or as_of
            pagination = payload.get('pagination') or {}
            if not pagination.get('has_cursor'):
                break
            next_cursor = pagination.get('cursor')
            if not next_cursor or next_cursor == cursor:
                raise ValueError('Bigdata MCP screener pagination is incomplete')
            cursor = next_cursor
        else:
            truncated = True
        members = [{'symbol': r['symbol'], 'rp_entity_id': r.get('rp_entity_id'),
                    'exchange': r.get('exchange_short_name') or r.get('exchange'),
                    'active': r.get('is_actively_trading'), 'valid_from': None, 'valid_to': None}
                   for r in rows if isinstance(r.get('symbol'), str)
                   and not r.get('is_etf') and not r.get('is_fund')]
        return encode({'members': members, 'as_of': as_of}).encode(), {
            'provider': self.name, 'transport': 'mcp', 'tool': 'bigdata_screen_companies',
            'membership': 'current_reference_snapshot', 'historical_membership': 'unverified',
            'pagination_truncated': truncated, 'observed_members': len(members), 'as_of': as_of}


class YahooUniverseConnector:
    name = 'yfinance_universe'

    def fetch(self, candidate):
        import yfinance as yf
        codes = {'NYSE': 'NYQ', 'AMEX': 'ASE', 'NASDAQ': 'NMS', 'XNYS': 'NYQ', 'XNAS': 'NMS'}
        exchanges = [codes[e] for e in candidate.request['exchanges'] if e in codes]
        if not exchanges:
            raise ValueError('No supported Yahoo exchange identifiers')
        query = yf.EquityQuery('and', [yf.EquityQuery('is-in', ['exchange', *exchanges]),
                                        yf.EquityQuery('eq', ['region', 'us'])])
        rows, truncated = [], False
        for page in range(MAX_PAGES):
            result = yf.screen(query, offset=page * 250, size=250, sortField='ticker', sortAsc=True)
            rows.extend(result.get('quotes', []))
            if (page + 1) * 250 >= result.get('total', len(rows)):
                break
        else:
            truncated = True
        members = [{'symbol': r['symbol'], 'rp_entity_id': None, 'exchange': r.get('exchange'),
                    'valid_from': None, 'valid_to': None}
                   for r in rows if r.get('quoteType') == 'EQUITY' and r.get('symbol')]
        return encode({'members': members, 'as_of': str(date.today())}).encode(), {
            'provider': self.name, 'membership': 'current_listings',
            'historical_membership': 'unverified', 'pagination_truncated': truncated,
            'observed_members': len(members), 'as_of': str(date.today())}


class UniverseResolver:
    def __init__(self, store, providers, notify):
        self.store, self.providers, self.notify = store, providers, notify

    def resolve(self, requirement):
        universe = definition(requirement)
        if not universe or not universe.exchanges:
            return [], [{'code': 'universe_definition_missing',
                         'message': 'No explicit identifiers or resolvable exchange definition'}]
        options, issues = [], []
        self.notify('universe_resolution_started', 'Resolving the paper universe', universe.model_dump())
        self.notify('data_capability_gap', 'Historical exchange membership is not established by current screeners',
                    {'required': universe.model_dump(), 'status': 'unverified'})
        for provider in self.providers:
            request = {'exchanges': sorted(set(e.upper() for e in universe.exchanges)),
                       'security_type': universe.security_type, 'version': 1}
            candidate = Candidate(digest({'provider': provider.name, **request}), provider.name,
                                  'Exchange security reference snapshot', request,
                                  getattr(provider, 'scope', 'public:yfinance'), 'universe')
            try:
                catalog = Catalog(self.store, lambda kind, detail: self.notify(kind, kind.replace('_', ' '), detail))
                dataset = catalog.acquire(candidate, provider.fetch)
                snapshot = json.loads(self.store.blob_get(dataset['key']))
                valid = {m['symbol']: m for m in snapshot['members']
                         if re.fullmatch(r'[A-Za-z0-9.^=_-]{1,24}', m['symbol'])}
                # Stable sampling by identifier, never by price or realized strategy performance.
                members = sorted(valid.values(), key=lambda m: digest(m['symbol']))[:MAX_MEMBERS]
                if not members:
                    raise ValueError('Universe provider returned no usable equity identifiers')
                provenance = {'provider': provider.name, 'dataset': dataset,
                              'observed_members': len(valid), 'selected_members': len(members),
                              'selection': 'sha256(identifier), first 100; independent of returns',
                              'historical_membership': 'unverified'}
                caveats = ['Universe is a current reference snapshot, not verified historical exchange membership. '
                           'Survivorship, exchange moves, common-share classification and delisted-security '
                           'coverage remain unverified. Provider exchange aliases do not prove historical membership.']
                if len(valid) > MAX_MEMBERS or dataset['metadata'].get('pagination_truncated'):
                    caveats.append('Bounded provider discovery and a maximum 100-security deterministic sample '
                                   'do not represent the complete original universe.')
                options.append({'members': members, 'provenance': provenance, 'caveats': caveats})
                self.notify('universe_resolved', 'Provider-backed substitute universe available', provenance)
                break
            except (ValueError, RuntimeError, OSError) as error:
                # Known local validation messages are safe; HTTP exceptions may contain headers/URLs.
                message = str(error) if isinstance(error, ValueError) else type(error).__name__
                issue = {'connector': provider.name, 'code': 'universe_unavailable', 'message': message[:240]}
                issues.append(issue)
                self.notify('data_discovery_issue', 'Universe source unavailable', issue)
        return options, issues
