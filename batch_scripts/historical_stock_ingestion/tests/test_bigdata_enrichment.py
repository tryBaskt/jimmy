import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('bigdata_enrichment', Path(__file__).resolve().parents[1] / 'bigdata_enrichment.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class SanitizeResearchTests(unittest.TestCase):
    def test_unknown_effective_date_stays_unknown_and_proprietary_fields_discarded(self):
        raw = {'companies':[{'symbol':'AAPL','company_name':'Apple','description':'must not retain',
            'classifications':[{'sector':'Information Technology','sector_code':'45','effective_from':None,
                'source_urls':['https://example.org/gics'], 'article_text':'must not retain'}], 'ticker_changes':[]}]}
        value = module.sanitize_research(raw,{'AAPL'},'2026-10-26T00:00:00Z')['AAPL']
        self.assertNotIn('description',value)
        row = value['classifications'][0]
        self.assertEqual(row['taxonomy'],'GICS')
        self.assertIsNone(row['effective_from'])
        self.assertEqual(row['date_basis'],'current_observation_only')
        self.assertNotIn('article_text',row)

    def test_unsourced_invalid_hierarchy_and_invalid_effective_dates_rejected(self):
        rows = [
            {'sector':'Information Technology','source_urls':[]},
            {'sector_code':'45','industry_code':'101010','source_urls':['https://example.org']},
            {'sector_code':'45','effective_from':'2020-02-30','source_urls':['https://example.org']},
            {'sector_code':'45','effective_from':None,'effective_to':'2020-01-01','source_urls':['https://example.org']},
        ]
        value = module.sanitize_research({'companies':[{'symbol':'AAPL','classifications':rows}]},{'AAPL'},'now')
        self.assertEqual(value['AAPL']['classifications'],[])

    def test_only_renames_with_sources_and_date_retained(self):
        changes = [
            {'old_symbol':'FB','new_symbol':'META','effective_date':'2022-06-09','source_urls':['https://about.fb.com/news/']},
            {'old_symbol':'GOOG','new_symbol':'GOOGL','effective_date':None,'source_urls':['https://example.org']},
            {'old_symbol':'X','new_symbol':'Y','effective_date':'2020-01-01','source_urls':['javascript:alert(1)']},
        ]
        value = module.sanitize_research({'companies':[{'symbol':'META','ticker_changes':changes}]},{'META'},'now')
        self.assertEqual(len(value['META']['ticker_changes']),1)
        self.assertEqual(value['META']['ticker_changes'][0]['verification_status'],'sourced_research_candidate')

    def test_current_dictionary_recovers_hierarchy_from_unambiguous_subindustry(self):
        taxonomy={'source_url':'https://www.msci.com/gics','codes':{
            '50':'Communication Services','5020':'Media & Entertainment',
            '502030':'Interactive Media & Services','50203010':'Interactive Media & Services'}}
        data={'companies':[{'symbol':'META','classifications':[{'sub_industry_code':'50203010',
            'source_urls':['https://example.org/classification'],'effective_from':None}]}]}
        row=module.sanitize_research(data,{'META'},'now',taxonomy)['META']['classifications'][0]
        self.assertEqual(row['industry_group'],'Media & Entertainment')
        self.assertEqual(row['industry_code'],'502030')
        self.assertIsNone(row['effective_from'])

    def test_conflicting_dated_snapshots_and_pre_gics_dates_rejected(self):
        rows=[{'sector_code':'45','effective_from':'1998-01-01','source_urls':['https://example.org']},
            {'sector_code':'45','effective_from':'2020-01-01','effective_to':'2022-01-01','source_urls':['https://example.org']},
            {'sector_code':'50','effective_from':'2021-01-01','source_urls':['https://example.org']}]
        result=module.sanitize_research({'companies':[{'symbol':'META','classifications':rows}]},{'META'},'now')['META']
        self.assertEqual(result['classifications'],[])
        self.assertTrue(result['classification_conflict'])

if __name__ == '__main__':
    unittest.main()
