#!/usr/bin/env python3
"""Resumable Bigdata enrichment; retain only classification and identity facts.

Knowledge Graph labels are provider taxonomy, never relabelled as GICS. Current
listings do not verify historical ticker identity. Research results are sourced
candidates for reconciliation, and unknown effective dates stay null. Neither
raw document text nor generated reports are persisted. Credentials stay runtime-only.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
import fcntl
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import re
import random
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

class BigdataError(RuntimeError):
    pass

class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def utc_now():
    return datetime.now(timezone.utc).isoformat()

def api_key(env_file: Path | None = None):
    value = os.getenv('BIGDATA_DEV_API_KEY') or os.getenv('BIGDATA_API_KEY')
    if value:
        return value
    if env_file and env_file.exists():
        # Parse literal values only. Do not execute a user environment file.
        for line in env_file.read_text().splitlines():
            key, sep, value = line.partition('=')
            if sep and key.strip() in {'BIGDATA_DEV_API_KEY', 'BIGDATA_API_KEY'}:
                value = value.strip().strip('"\'')
                if value:
                    return value
    raise BigdataError('Set BIGDATA_DEV_API_KEY or supply a local environment file')

def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as handle:
            json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write('\n')
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)

def source_urls(values):
    if not isinstance(values, list):
        return []
    return sorted({v for v in values if isinstance(v, str) and len(v) < 2048
                   and urlsplit(v).scheme in {'https', 'http'} and urlsplit(v).hostname})

def iso_date(value):
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('invalid date')
    date.fromisoformat(value)
    return value

# S&P DJI GICS landing page, https://www.spglobal.com/spdji/en/landing/topic/gics/
GICS_SECTORS = {'10':'Energy','15':'Materials','20':'Industrials','25':'Consumer Discretionary',
 '30':'Consumer Staples','35':'Health Care','40':'Financials','45':'Information Technology',
 '50':'Communication Services','55':'Utilities','60':'Real Estate'}

def normalized_label(value):
    return re.sub(r'[^a-z0-9]', '', str(value).lower().replace('&','and'))

GICS_FIELDS = ('sector_code', 'sector', 'industry_group_code', 'industry_group',
               'industry_code', 'industry', 'sub_industry_code', 'sub_industry')

def research_schema():
    nullable = {'type': ['string', 'null']}
    classification = {k: nullable for k in GICS_FIELDS}
    classification.update({'effective_from': dict(nullable, description='Explicit classification effective date only; null if only current observation is known.'),
                           'effective_to': nullable,
                           'source_urls': {'type': 'array', 'items': {'type': 'string'}}})
    change = {k: {'type': 'string'} for k in ('old_symbol', 'new_symbol', 'effective_date')}
    change['source_urls'] = {'type': 'array', 'items': {'type': 'string'}}
    return {'type': 'object', 'properties': {'companies': {'type': 'array', 'items': {
        'type': 'object', 'properties': {
            'symbol': {'type': 'string'}, 'company_name': nullable,
            'classifications': {'type': 'array', 'items': {'type': 'object', 'properties': classification,
                                                        'required': ['source_urls']}},
            'identities': {'type': 'array', 'items': {'type':'object', 'properties': {'company_name':{'type':'string'}, 'historical_symbol_valid_from':nullable, 'historical_symbol_valid_to':nullable, 'ticker_reuse_checked':{'type':'boolean'}, 'source_urls':{'type':'array','items':{'type':'string'}}}, 'required':['company_name','historical_symbol_valid_from','historical_symbol_valid_to','ticker_reuse_checked','source_urls']}},
            'ticker_changes': {'type': 'array', 'items': {'type': 'object', 'properties': change,
                                                       'required': list(change)}}},
        'required': ['symbol', 'classifications', 'ticker_changes']}}}, 'required': ['companies']}

def sanitize_research(data, requested, observed_at, taxonomy=None):
    """Whitelist facts and require sources, valid dates/codes, and requested symbols."""
    result = {}
    for row in data.get('companies', []):
        symbol = row.get('symbol')
        if symbol not in requested or symbol in result:
            continue
        out = {'symbol': symbol, 'company_name': row.get('company_name') if isinstance(row.get('company_name'), str) else None,
               'classifications': [], 'ticker_changes': [], 'identities': [], 'observed_at': observed_at,
               'verification_status': 'sourced_research_candidate'}
        for identity in row.get('identities', []):
            urls = source_urls(identity.get('source_urls'))
            try:
                valid_from = iso_date(identity.get('historical_symbol_valid_from'))
                valid_to = iso_date(identity.get('historical_symbol_valid_to'))
                if valid_from and valid_to and valid_to <= valid_from:
                    continue
            except ValueError:
                continue
            if urls and isinstance(identity.get('company_name'), str):
                out['identities'].append({'company_name':identity['company_name'],
                    'historical_symbol_valid_from':valid_from,'historical_symbol_valid_to':valid_to,
                    'ticker_reuse_checked':identity.get('ticker_reuse_checked') is True,
                    'source_urls':urls,'verification_status':'sourced_research_candidate'})
        for item in row.get('classifications', []):
            urls = source_urls(item.get('source_urls'))
            if not urls:
                continue
            fields = {k: str(item[k]).strip() for k in GICS_FIELDS if item.get(k) is not None}
            dictionary_used = False
            # Only apply the current dictionary to current/new observations. Historical
            # classifications must be validated against their historical structure.
            if taxonomy and (not item.get('effective_from') or item['effective_from'] >= '2023-03-18'):
                codes_dict = taxonomy['codes']
                for label, size in [('sector',2),('industry_group',4),('industry',6),('sub_industry',8)]:
                    key = label + '_code'
                    if not fields.get(key) and fields.get(label):
                        matches=[code for code,name in codes_dict.items() if len(code)==size and normalized_label(name)==normalized_label(fields[label])]
                        if len(matches)==1:
                            fields[key]=matches[0]
                longest = max((v for k,v in fields.items() if k.endswith('_code') and v in codes_dict),key=len,default='')
                for label,size in [('sector',2),('industry_group',4),('industry',6),('sub_industry',8)]:
                    code=longest[:size]
                    if len(code)==size and code in codes_dict:
                        fields[label+'_code']=code
                        fields[label]=codes_dict[code]
                        dictionary_used=True
            if not fields:
                continue
            invalid = any(not re.fullmatch(r'\d{' + str(size) + '}', fields[key])
                          for key, size in [('sector_code', 2), ('industry_group_code', 4), ('industry_code', 6), ('sub_industry_code', 8)] if key in fields)
            codes = [fields[k] for k in ('sector_code','industry_group_code','industry_code','sub_industry_code') if k in fields]
            sector_code = fields.get('sector_code')
            sector_label = fields.get('sector')
            bad_sector = sector_code and (sector_code not in GICS_SECTORS or
                (sector_label and normalized_label(sector_label) != normalized_label(GICS_SECTORS[sector_code])
                 and not (sector_code == '50' and normalized_label(sector_label) == 'telecommunicationservices')))
            if bad_sector or invalid or any(not b.startswith(a) for a,b in zip(codes, codes[1:])):
                continue
            try:
                start, end = iso_date(item.get('effective_from')), iso_date(item.get('effective_to'))
                if start and start < '1999-01-01':
                    continue
                if end and (not start or end <= start):
                    continue
            except ValueError:
                continue
            out['classifications'].append(dict(fields, taxonomy='GICS', effective_from=start,
                effective_to=end, observed_at=observed_at, source_urls=urls,
                date_basis='confirmed_effective_date' if start else 'current_observation_only',
                verification_status='sourced_research_candidate',
                taxonomy_source_url=taxonomy['source_url'] if dictionary_used else None))
        for item in row.get('ticker_changes', []):
            urls = source_urls(item.get('source_urls'))
            try:
                when = iso_date(item.get('effective_date'))
            except ValueError:
                continue
            old, new = item.get('old_symbol'), item.get('new_symbol')
            if not urls or not when or old == new or not all(isinstance(s, str) and re.fullmatch(r'[A-Z0-9][A-Z0-9.\-^]*', s) for s in (old,new)):
                continue
            out['ticker_changes'].append({'old_symbol': old, 'new_symbol': new, 'effective_date': when,
                'source_urls': urls, 'verification_status': 'sourced_research_candidate'})
        dated = sorted((r for r in out['classifications'] if r['effective_from']), key=lambda r:r['effective_from'])
        conflicting = set()
        for left, right in zip(dated,dated[1:]):
            if left['effective_from'] == right['effective_from'] or (left['effective_to'] and left['effective_to'] > right['effective_from']):
                conflicting.update((id(left),id(right)))
        if conflicting:
            out['classifications'] = [r for r in out['classifications'] if id(r) not in conflicting]
            out['classification_conflict'] = True
        result[symbol] = out
    return result

class BigdataClient:
    def __init__(self, key, timeout=600):
        self.key, self.timeout = key, timeout
        self.opener = build_opener(NoRedirects())

    def request(self, host, path, payload=None):
        body = json.dumps(payload).encode() if payload is not None else None
        request = Request('https://' + host + path, data=body,
                          headers={'X-API-KEY': self.key, 'Content-Type': 'application/json'})
        for attempt in range(5):
            try:
                return self.opener.open(request, timeout=self.timeout)
            except HTTPError as error:
                status = error.code
                retry_after = error.headers.get('Retry-After', '')
                error.close()
                if status in {429, 500, 502, 503, 504} and attempt < 4:
                    delay = min(float(retry_after), 60) if retry_after.isdigit() else min(2 ** (attempt + 1), 30)
                    time.sleep(delay + random.random())
                    continue
                raise BigdataError(f'Bigdata HTTP {status}; provider body omitted') from None
            except (URLError, TimeoutError, OSError):
                # Research POSTs may already have run/billed: never auto-retry them.
                if host == 'api.bigdata.com' and attempt < 4:
                    time.sleep(2 ** attempt)
                    continue
                raise BigdataError('Bigdata network request failed; no provider body retained') from None

    def quota(self):
        with self.request('api.bigdata.com', '/v1/subscription/quotas') as response:
            data = json.load(response)['results']
        return {k: data.get('credits', {}).get(k) for k in ('limit', 'usage', 'remaining')}

    def find_companies(self, symbol):
        with self.request('api.bigdata.com', '/v1/knowledge-graph/companies',
                          {'query': symbol, 'types': ['PUBLIC']}) as response:
            data = json.load(response)
        # Explicit allowlist discards descriptions, snippets, favicons, and document content.
        keys = ('id', 'name', 'country', 'sector', 'industry_group', 'industry', 'listing_values')
        companies = [{k: row[k] for k in keys if k in row} for row in data.get('results', [])]
        exact = [r for r in companies if any(v.split(':',1)[-1] == symbol for v in r.get('listing_values', []))]
        return {'symbol': symbol, 'observed_at': utc_now(), 'provider_taxonomy': 'Bigdata provider classification; not GICS',
                'identity_status': 'current_listing_candidate' if exact else 'unresolved',
                'historical_identity_verified': False, 'exact_listing_candidates': exact,
                'candidates': companies}

    def research_history(self, symbols, start, end, contexts=None, current_only=False):
        prompt = ('Build factual company identity/ticker and full GICS classification CHANGE HISTORY records for these historical US S&P 500 source tickers: '
                  + ', '.join(symbols) + f'. Historical period {start} through {end}. '
                  'For each security produce a distinct classification row at EVERY historical GICS change, preserving prior classifications. GICS began in 1999, so never create pre-1999 GICS rows. Resolve delisted tickers using this historical context; ticker reuse and share classes must not be conflated. '
                  'Use sources to identify actual GICS sector/industry/subindustry (not proprietary vendor or FMP sector/industry labels). '
                  'Include historical GICS changes only with explicit effective dates; if only current classifications are available, '
                  'set effective_from/effective_to null. Include confirmed ticker rename effective dates; do not treat acquisitions, '
                  'mergers creating a different security, or different share classes as renames. Each record needs source URLs '
                  'supporting that record. Also return identities with company_name, dated historical symbol ownership ranges and primary source URLs so each historical ticker episode can be assigned to the correct company. historical_symbol_valid_to is exclusive. Mark ticker_reuse_checked true only after explicitly checking for other companies using that symbol during requested ranges. Match classifications to the named company. Prefer issuer, exchange, S&P and MSCI primary announcements. Do not infer classifications '
                  'from business descriptions. Leave missing information empty. Return only requested classification/identity facts, '
                  'not quotes, snippets, article summaries, financial data, or other proprietary content. Unresolved companies have empty arrays.')
        if current_only:
            prompt = ('Retrieve ONLY the CURRENT explicitly named GICS classifications for the exact public company/security in the contexts below. '
                      'Return one row per requested symbol, company_name, and at most one classification. '
                      'Do not research history or ticker changes. Do not infer classification from products/business descriptions. '
                      'Do not relabel provider/FMP sector/industry as GICS. Include exact GICS sector, industry group, industry and subindustry labels/codes '
                      'when explicitly available; missing values null. Set effective_from and effective_to null; this is a current observation, never a historical effective date. '
                      'Provide a source URL explicitly documenting each classification. identities/ticker_changes empty. Prefer S&P/MSCI or issuer sources. '
                      'No snippets, quotes, reports, document text, financial data, or unrequested content. Requested symbols: ' + ', '.join(symbols))
        if contexts:
            prompt += ' Per-symbol observed CSV episodes and current KG company candidates (these are clues, not evidence of historical identity): ' + json.dumps(contexts)
        payload = {'message': prompt, 'model_name': 'base', 'research_effort': 'lite' if current_only else 'standard',
                   'persistence_mode': 'disabled', 'structured_output_schema': research_schema()}
        output = None
        complete = False
        with self.request('agents.bigdata.com', '/v1/research-agent', payload) as response:
            for line in response:
                if not line.startswith(b'data:'):
                    continue
                try:
                    event = json.loads(line[5:].strip()).get('message', {})
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                kind = event.get('type')
                if kind == 'STRUCTURED_OUTPUT':
                    output = event.get('content')
                elif kind == 'ERROR':
                    raise BigdataError('Bigdata research stream reported an error; provider message omitted')
                elif kind == 'COMPLETE':
                    complete = True
                # Deliberately discard ANSWER, AUDIT and document-bearing events.
        if not complete or not isinstance(output, dict):
            raise BigdataError('Bigdata research incomplete or structured output unavailable')
        dictionary_path = Path(__file__).resolve().parent / 'artifacts' / 'gics_dictionary.json'
        taxonomy = json.loads(dictionary_path.read_text()) if dictionary_path.exists() else None
        return sanitize_research(output, set(symbols), utc_now(), taxonomy)

def symbols_from_csv(path):
    with open(path, newline='', encoding='utf-8-sig') as handle:
        rows = list(csv.DictReader(handle))
    symbols = sorted({s.strip() for row in rows for s in row['tickers'].split(',') if s.strip()})
    return symbols, min(row['date'] for row in rows), max(row['date'] for row in rows)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--csv', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--env-file', type=Path, default=Path(__file__).resolve().parents[2] / '.env')
    parser.add_argument('--current-only', action='store_true', help='Narrow lite current GICS observation pass; use separate output file from history')
    parser.add_argument('--symbols', help='Optional comma-separated symbols to prioritize research')
    parser.add_argument('--kg', action='store_true')
    parser.add_argument('--research', action='store_true')
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--batch-size', type=int, default=20)
    parser.add_argument('--research-batches', type=int, default=4)
    parser.add_argument('--credit-budget', type=float, default=4000)
    args = parser.parse_args()
    if not 1 <= args.workers <= 8 or not 1 <= args.batch_size <= 30 or args.research_batches < 0 or args.credit_budget <= 0:
        parser.error('invalid concurrency, batch size or budget')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_lock = args.output.with_suffix(args.output.suffix + '.lock').open('a')
    try:
        fcntl.flock(checkpoint_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise BigdataError('Another enrichment process is writing this checkpoint') from None
    client = BigdataClient(api_key(args.env_file))
    symbols, start, end = symbols_from_csv(args.csv)
    contexts = {s:{'observed_from':None,'observed_to':None} for s in symbols}
    with args.csv.open(newline='',encoding='utf-8-sig') as handle:
        for row in csv.DictReader(handle):
            for symbol in row['tickers'].split(','):
                symbol=symbol.strip()
                context=contexts[symbol]
                context['observed_from']=min(context['observed_from'] or row['date'],row['date'])
                context['observed_to']=max(context['observed_to'] or row['date'],row['date'])
    state = json.loads(args.output.read_text()) if args.output.exists() else {'version': 1, 'kg': {}, 'research': {}, 'research_batches': [], 'errors': {}}
    quota = client.quota()
    state.setdefault('initial_credit_usage', quota['usage'])
    state['quota'] = quota
    def budget_ok():
        q = client.quota()
        state['quota'] = q
        atomic_json(args.output, state)
        if q['usage'] is None or q['remaining'] is None:
            raise BigdataError('Credit allowance unavailable; stopped before bulk calls')
        return q['usage'] - state['initial_credit_usage'] < args.credit_budget - 100 and q['remaining'] > 100
    if args.kg:
        pending = [s for s in symbols if s not in state['kg']]
        for offset in range(0, len(pending), 40):
            if not budget_ok():
                print('Credit budget reached; progress saved', flush=True)
                return
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                futures = {pool.submit(client.find_companies,s): s for s in pending[offset:offset+40]}
                for future in as_completed(futures):
                    symbol = futures[future]
                    try:
                        state['kg'][symbol] = future.result()
                        state['errors'].pop(symbol, None)
                    except BigdataError as error:
                        state['errors'][symbol] = str(error)
                    atomic_json(args.output, state)
            print(f'KG {len(state["kg"])}/{len(symbols)}; unresolved {sum(not r["exact_listing_candidates"] for r in state["kg"].values())}', flush=True)
    if args.research:
        # Prioritize historical symbols unresolved in current KG; each symbol attempted once.
        attempted = {s for batch in state['research_batches'] for s in batch['symbols']}
        pending = sorted((s for s in symbols if s not in attempted), key=lambda s:(bool(state['kg'].get(s,{}).get('exact_listing_candidates')),s))
        for offset in range(0,min(len(pending),args.research_batches*args.batch_size),args.batch_size):
            if not budget_ok():
                print('Credit budget reached; progress saved', flush=True)
                return
            if args.symbols:
                priority=args.symbols.split(',')
                pending.sort(key=lambda s:(s not in priority,priority.index(s) if s in priority else 0,s))
            batch = pending[offset:offset+args.batch_size]
            try:
                result = client.research_history(batch,start,end,{s:dict(contexts[s],current_company_candidates=[r.get('name') for r in state['kg'].get(s,{}).get('exact_listing_candidates',[])]) for s in batch},current_only=args.current_only)
            except BigdataError as error:
                state['last_research_error'] = str(error)
                atomic_json(args.output,state)
                raise
            observations = state.setdefault('research_observations', {})
            for symbol, row in result.items():
                observations.setdefault(symbol, [])
                previous = state['research'].get(symbol)
                if previous and previous not in observations[symbol]:
                    observations[symbol].append(previous)
                observations[symbol].append(row)
                # Aggregate distinct extracted states without deleting any prior research.
                if previous:
                    for key in ('classifications','ticker_changes','identities'):
                        merged = list(previous.get(key,[]))
                        for fact in row.get(key,[]):
                            if fact not in merged:
                                merged.append(fact)
                        row[key] = merged
            state['research'].update(result)
            state['research_batches'].append({'symbols':batch,'returned':len(result),'completed_at':utc_now()})
            atomic_json(args.output,state)
            print(f'Research {len(state["research"])}/{len(symbols)}; GICS rows {sum(len(r["classifications"]) for r in state["research"].values())}; ticker changes {sum(len(r["ticker_changes"]) for r in state["research"].values())}',flush=True)
    budget_ok()
    print(json.dumps({'kg_symbols':len(state['kg']),'research_symbols':len(state['research']),'credits':state['quota']}))

if __name__ == '__main__':
    try:
        main()
    except BigdataError as error:
        raise SystemExit(str(error))
