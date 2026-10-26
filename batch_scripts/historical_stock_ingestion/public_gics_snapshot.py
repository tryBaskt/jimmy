#!/usr/bin/env python3
"""Deterministically extract current public S&P 500 GICS observations.

This is a current observation, not historical membership or an effective-date
feed. CIK identifies the issuer and does not unify different share classes.
Only factual table columns are retained; the downloaded HTML is not cached.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from urllib.request import Request, urlopen

SOURCE_URL = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'

class ConstituentsParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_table = False
        self.table_depth = 0
        self.in_cell = False
        self.cell = []
        self.row = []
        self.rows = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == 'table':
            if not self.in_table and attributes.get('id') == 'constituents':
                self.in_table = True
            if self.in_table:
                self.table_depth += 1
        if self.in_table and tag == 'tr':
            self.row = []
        if self.in_table and tag in {'td', 'th'}:
            self.in_cell = True
            self.cell = []

    def handle_data(self, value):
        if self.in_table and self.in_cell:
            self.cell.append(value)

    def handle_endtag(self, tag):
        if not self.in_table:
            return
        if tag in {'td', 'th'} and self.in_cell:
            self.row.append(' '.join(''.join(self.cell).split()))
            self.in_cell = False
        if tag == 'tr' and self.row:
            self.rows.append(self.row)
        if tag == 'table':
            self.table_depth -= 1
            if self.table_depth == 0:
                self.in_table = False


def normalized(value):
    return re.sub(r'[^a-z0-9]', '', value.lower().replace('&','and'))


def extract(html, dictionary, observed_at):
    parser = ConstituentsParser()
    parser.feed(html)
    if not parser.rows or parser.rows[0][:4] != ['Symbol','Security','GICS Sector','GICS Sub-Industry']:
        raise ValueError('Public constituent table schema changed')
    records, issues = {}, []
    for row in parser.rows[1:]:
        if len(row) != 8:
            raise ValueError('Unexpected current constituent row shape')
        symbol, name, sector, subindustry, headquarters, added, cik, founded = row
        matches = [code for code,label in dictionary['codes'].items()
                   if len(code) == 8 and normalized(label) == normalized(subindustry)]
        if len(matches) != 1:
            issues.append({'symbol':symbol,'issue':'subindustry_not_in_current_dictionary','source_sub_industry':subindustry})
            continue
        code = matches[0]
        if normalized(dictionary['codes'][code[:2]]) != normalized(sector):
            issues.append({'symbol':symbol,'issue':'source_sector_conflicts_with_subindustry'})
            continue
        if symbol in records or not re.fullmatch(r'\d{10}',cik):
            raise ValueError('Duplicate symbol or invalid CIK in current source')
        classification = {'taxonomy':'GICS','effective_from':None,'effective_to':None,
                          'observed_at':observed_at,'date_basis':'current_observation_only',
                          'source_urls':[SOURCE_URL], 'taxonomy_source_url':dictionary['source_url'],
                          'verification_status':'deterministic_source_extraction'}
        for label,size in [('sector',2),('industry_group',4),('industry',6),('sub_industry',8)]:
            classification[label+'_code']=code[:size]
            classification[label]=dictionary['codes'][code[:size]]
        records[symbol] = {'symbol':symbol,'company_name':name,'cik':cik,
                           'source_security_name':name,'source_urls':[SOURCE_URL],
                           'observed_at':observed_at,'historical_identity_verified':False,
                           'identities':[],'ticker_changes':[], 'classifications':[classification],
                           'verification_status':'deterministic_source_extraction'}
    if not 450 <= len(records) <= 550:
        raise ValueError('Unexpected current constituent coverage')
    return {'version':1,'source_url':SOURCE_URL,'observed_at':observed_at,
            'scope':'Current public source observation; no historical effective dates or share-class merges inferred',
            'research':records,'issues':issues}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dictionary',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    dictionary=json.loads(args.dictionary.read_text())
    request=Request(SOURCE_URL,headers={'User-Agent':'Jimmy research ingestion (current factual classification snapshot)'})
    with urlopen(request,timeout=60) as response:
        html=response.read().decode('utf-8')
    result=extract(html,dictionary,datetime.now(timezone.utc).isoformat())
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'current_classification_observations':len(result['research']),'issues':len(result['issues'])}))

if __name__=='__main__':
    main()
