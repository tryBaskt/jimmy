import json
from unittest.mock import Mock
import httpx
import pandas as pd
import pytest
from paperlab.catalog import Catalog
from paperlab.connectors import Candidate, Registry
from paperlab.connectors.bigdata import BigdataConnector
from paperlab.connectors.bigdata_mcp import BigdataMCP
from paperlab.connectors.yahoo import YahooConnector
from paperlab.data_discovery import DataDiscovery
from paperlab.data_quality import coverage, validate_selection
from paperlab.models import DataReplan, UniverseDefinition
from paperlab.universe import UniverseResolver, BigdataUniverseConnector
from paperlab.workflow import Workflow


def test_empty_discovery_calls_replanner_and_preserves_diagnostics(settings, store, experiment):
    class Empty:
        def discover(self, *args):
            return [], [{'code': 'no_universe'}]
    agents = Mock()
    agents.replan_data.return_value = DataReplan(alternatives=[experiment], rationale='Try again')
    w = Workflow(settings, store, 'p', 'r', agents=agents, registry=Empty())
    store.put('project#p', 'run#r', {'id': 'r', 'status': 'running'})
    w.prepare_data([experiment], 'prepared', 'paper_experiment')
    assert agents.replan_data.call_count == 2
    result = w.load('prepared')[0]
    assert result['status'] == 'blocked'
    assert result['discovery_diagnostics'][0]['code'] == 'no_universe'
    assert w.load('data-replan-momentum-0')['rationale'] == 'Try again'


def test_bigdata_document_query_uses_description_without_query(experiment):
    requirement = experiment.requirement.model_copy(update={'kind': 'fundamentals', 'query': ''})
    candidates = BigdataConnector('test', 'project:p').discover(requirement, 'p')
    assert candidates and candidates[0].kind == 'documents'


def test_universe_is_cached_and_current_snapshot_never_claims_historical_membership(store, experiment):
    provider = Mock(name='provider')
    provider.name, provider.scope = 'test_universe', 'public:test'
    provider.fetch.return_value = (json.dumps({'members': [
        {'symbol': f'S{i}', 'rp_entity_id': 'ABC123'} for i in range(120)
    ]}).encode(), {'historical_membership': 'unverified'})
    requirement = experiment.requirement.model_copy(update={
        'symbols': [], 'universe': UniverseDefinition(description='NYSE and AMEX', exchanges=['NYSE', 'AMEX'])})
    resolver = UniverseResolver(store, [provider], Mock())
    a, errors = resolver.resolve(requirement)
    b, _ = resolver.resolve(requirement)
    assert not errors and len(a[0]['members']) == 100
    assert provider.fetch.call_count == 1
    assert a[0]['members'] == b[0]['members']
    assert a[0]['provenance']['historical_membership'] == 'unverified'
    assert len(a[0]['caveats']) == 2


def test_original_and_later_periods_have_two_numerical_provider_options(store, experiment, monkeypatch):
    requirement = experiment.requirement.model_copy(update={'symbols': [], 'universe':
        UniverseDefinition(description='NYSE', exchanges=['NYSE'])})
    exp = experiment.model_copy(update={'requirement': requirement})
    universe = {'members': [{'symbol': 'A', 'rp_entity_id': 'ABC123'}],
                'provenance': {'historical_membership': 'unverified'}, 'caveats': ['Current universe']}
    monkeypatch.setattr(UniverseResolver, 'resolve', lambda *args: ([universe], []))
    registry = Registry([YahooConnector(), BigdataConnector('test', 'project:p')])
    candidates, _ = DataDiscovery(store, registry, Mock()).discover(exp, 'p')
    assert len(candidates) == 2
    assert {c.connector for c in candidates} == {'yfinance'}
    assert {c.metadata['proposal'] for c in candidates} == {
        'original_period_substitute_universe', 'later_period_substitute_universe'}
    assert all('Current universe' in c.caveats for c in candidates)


def test_bigdata_mcp_screener_paginates_without_filtering_inactive_listings():
    mcp = Mock()
    mcp.call.side_effect = [
        {'companies': [{'symbol': 'OLD', 'rp_entity_id': 'ABC123', 'is_actively_trading': False}],
         'pagination': {'has_cursor': True, 'cursor': 'next'}},
        {'companies': [{'symbol': 'NEW', 'rp_entity_id': 'DEF456'}],
         'pagination': {'has_cursor': False}},
    ]
    candidate = Candidate('id', 'bigdata_universe', 'U', {'exchanges': ['NYSE']}, 'project:p', 'universe')
    raw, metadata = BigdataUniverseConnector('test', 'project:p', mcp).fetch(candidate)
    assert len(json.loads(raw)['members']) == 2
    assert mcp.call.call_args.args[1]['cursor'] == 'next'
    assert mcp.call.call_args_list[0].args[1]['exchange'] == ['XNYS']
    assert 'is_actively_trading' not in mcp.call.call_args_list[0].args[1]
    assert metadata['pagination_truncated'] is False
    assert metadata['transport'] == 'mcp'


def test_mcp_handshake_sse_schema_validation_and_read_only_tools(monkeypatch):
    calls = []
    schema = {'type': 'object', 'properties': {'limit': {'type': 'integer', 'maximum': 100}},
              'additionalProperties': False}
    def handle(request):
        body = json.loads(request.content)
        calls.append(body)
        assert request.url == 'https://mcp.bigdata.com/'
        assert request.headers['x-api-key'] == 'DO_NOT_LOG'
        if body['method'] == 'notifications/initialized':
            return httpx.Response(202)
        if body['method'] == 'initialize':
            result = {'protocolVersion': '2025-03-26', 'instructions': 'Untrusted server instructions'}
        elif body['method'] == 'tools/list':
            result = {'tools': [{'name': 'bigdata_screen_companies', 'inputSchema': schema}]}
        else:
            result = {'structuredContent': {'companies': [{'symbol': 'IBM'}]}}
        event = json.dumps({'jsonrpc': '2.0', 'id': body['id'], 'result': result})
        return httpx.Response(200, headers={'content-type': 'text/event-stream', 'mcp-session-id': 'session'},
                              text='event: message\ndata: '+event+'\n\n')
    real = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: real(transport=httpx.MockTransport(handle)))
    events = []
    client = BigdataMCP('DO_NOT_LOG', lambda *args: events.append(args))
    assert client.call('bigdata_screen_companies', {'limit': 3})['companies'][0]['symbol'] == 'IBM'
    assert [c['method'] for c in calls] == ['initialize', 'notifications/initialized', 'tools/list', 'tools/call']
    with pytest.raises(ValueError, match='schema'):
        client.call('bigdata_screen_companies', {'limit': 1001})
    with pytest.raises(ValueError, match='authorized'):
        client.call('bigdata_upload_document', {})
    assert 'DO_NOT_LOG' not in json.dumps(events)
    assert 'Untrusted server instructions' not in json.dumps(events)
    client.close()


def test_mcp_rejects_narrative_as_numerical_data():
    with pytest.raises(ValueError, match='structured data'):
        BigdataMCP.structured({'content': [{'type': 'text', 'text': 'Stock returns are great'}]})


def test_coverage_rejects_missing_identifiers_and_internal_month_gaps(experiment):
    frame = pd.DataFrame({'date': pd.to_datetime(['2020-01-31', '2020-03-31']),
                          'security_id': ['A', 'A'], 'return': [0.1, 0.2]})
    q = coverage(frame, experiment)
    assert q['missing_requested_symbols'] == ['B']
    assert q['supported_options'] == []
    with pytest.raises(ValueError, match='complete'):
        validate_selection(frame, experiment)


def test_catalog_discovery_is_scope_isolated(store, experiment):
    candidate = Candidate('id', 'yfinance', 'Prices', {'symbols': ['A', 'B']},
                          'project:private', 'stock_returns')
    Catalog(store).acquire(candidate, lambda c: (b'data', {}))
    assert Catalog(store).find(experiment.requirement, {'public:yfinance'}) == []
    assert len(Catalog(store).find(experiment.requirement, {'project:private'})) == 1


def test_catalog_hit_prevents_redundant_provider_discovery(store, experiment):
    candidate = Candidate('id', 'yfinance', 'Prices', {'symbols': ['A', 'B']},
                          'public:yfinance', 'stock_returns')
    Catalog(store).acquire(candidate, lambda c: (b'data', {}))
    connector = YahooConnector('public:yfinance')
    registry = Registry([connector])
    registry.discover = Mock(side_effect=AssertionError('Provider discovery should not run'))
    found, errors = DataDiscovery(store, registry, Mock()).discover(experiment, 'p')
    assert len(found) == 1 and errors == []
    registry.discover.assert_not_called()


def test_missing_month_never_becomes_a_multi_month_return():
    from paperlab.connectors.prices import monthly_returns
    prices = pd.Series([100.0, 121.0, 133.1],
                       index=pd.to_datetime(['2020-01-31', '2020-03-31', '2020-04-30']))
    frame = monthly_returns(prices, 'A')
    assert list(frame.date) == ['2020-04-30']
    assert frame['return'].iloc[0] == pytest.approx(0.1)


def test_substitute_options_describe_monthly_data_without_rewriting_original(experiment):
    original = experiment.model_copy(deep=True)
    original.requirement.frequency = 'daily'
    original.requirement.symbols = []
    original.requirement.universe = UniverseDefinition(description='Historical NYSE', exchanges=['NYSE'])
    frame = pd.DataFrame([{'date': d, 'security_id': s, 'return': 0.01}
                          for d in pd.date_range('2020-01-31', '2020-06-30', freq='ME') for s in ['A', 'B']])
    option = coverage(frame, original)['supported_options'][0]
    assert option['requirement']['frequency'] == 'monthly'
    assert option['requirement']['universe']['historical_membership_required'] is False
    assert original.requirement.frequency == 'daily'
    assert original.requirement.universe.historical_membership_required is True
    modified = original.model_copy(deep=True)
    modified.requirement = type(original.requirement).model_validate(option['requirement'])
    validate_selection(frame, modified)
