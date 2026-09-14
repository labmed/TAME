"""Regression checks for chart identity, complete paged audits and display profiles."""
from pathlib import Path
import csv
import io
import json
from hashlib import sha256
import tempfile
import unittest
import zipfile

import pandas as pd
from fastapi.testclient import TestClient
from tametools.io import read_tame
from tametools.reporting import chart_spec, with_visualizations
from tametools.models import ColumnSpec, TameDataset
from app.main import app
from app.tametools_bridge import chart_payloads, dataset_from_payload, dataset_payload, profile_payloads
from app.workbench_analysis import reference_analysis


class ReviewV2Tests(unittest.TestCase):
    def test_six_charts_match_each_analyte_limits_cis_and_saved_tame(self):
        with TestClient(app) as client:
            source=client.get('/api/examples/kenya').json()
            result=reference_analysis(source,{'OUTLIER_METHOD':'NONE'})
            self.assertEqual(result['issues'],[])
            self.assertEqual(len(result['charts']),6)
            for chart in result['charts']:
                self.assertEqual(chart['type'],'interval')
                cfg=dataset_from_payload(result,standardize=False).meta['VISUALIZATIONS'][chart['name']]
                identifier=cfg['FILTER']['result_id']
                rows=[r for r in result['analysisTables']['reference_intervals'] if r['result_id']==identifier and r['primary']]
                self.assertEqual(len(rows),len(chart['rows']))
                for actual,expected in zip(chart['rows'],rows):
                    for dest,key in [('low','ref_low'),('high','ref_high'),('lowCiLow','low_ci_low'),('highCiHigh','high_ci_high')]:
                        self.assertEqual(actual[dest],expected[key], (identifier,key))
            saved=client.post('/api/datasets/tame',json=result)
            with tempfile.TemporaryDirectory() as d:
                p=Path(d)/'result.tame';p.write_bytes(saved.content)
                self.assertEqual(chart_payloads(read_tame(p)),result['charts'])

    def test_paged_audits_match_complete_csv_and_zip_without_initial_rows(self):
        with TestClient(app) as client:
            source=client.get('/api/examples/kenya').json()
            response=client.post('/api/datasets/reference-interval',json={'dataset':source,'options':{'RESULT_IDS':['alt'],'OUTLIER_METHOD':'NONE'}})
            self.assertEqual(response.status_code,200,response.text[:500])
            result=response.json()
            self.assertNotIn('input_audit',result['analysisTables'])
            self.assertNotIn('group_membership',result['analysisTables'])
            self.assertEqual(result['analysisSettings']['OUTLIER_WARN_RATE'],.02)
            with zipfile.ZipFile(io.BytesIO(client.get(result['reportFiles'][1]['url']).content)) as z:
                for name,info in result['analysisTableFiles'].items():
                    content=client.get(info['downloadUrl']).content
                    self.assertEqual(content,z.read('tables/'+name+'.csv'))
                    expected=list(csv.DictReader(io.StringIO(content.decode('utf-8-sig'))))
                    self.assertEqual(info['rowCount'],len(expected))
                    actual=[]
                    for offset in range(0,info['rowCount'],200):
                        page=client.get(info['pageUrl'],params={'offset':offset,'limit':200})
                        self.assertEqual(page.status_code,200,page.text[:300])
                        actual.extend(page.json()['rows'])
                    self.assertEqual(actual,expected)
                    self.assertEqual(client.get(info['pageUrl'],params={'offset':-1}).status_code,400)
                    self.assertEqual(client.get(info['pageUrl'],params={'limit':10000}).status_code,400)
                manifest=json.loads(z.read('manifest.json'))
                self.assertEqual(manifest['versions']['tametools'],'0.4.0')
                self.assertEqual({i['path'] for i in manifest['files']},set(z.namelist())-{'manifest.json'})
                for item in manifest['files']:self.assertEqual(item['sha256'],sha256(z.read(item['path'])).hexdigest())
            self.assertEqual(client.get('/api/analysis-tables/invalid/input_audit').status_code,404)

    def test_scientific_notation_is_valid_numeric_in_memory_and_after_serialization(self):
        from tametools.analysis import parse_strict_number, parse_comparator_number, validate_dataset, numeric_series_for
        for text in ['4.2e-28','1E+6','-.25e-2']:
            self.assertEqual(parse_strict_number(text),float(text))
            self.assertEqual(parse_comparator_number('<'+text),('<',float(text)))
        for text in ['nan','inf','1e999','1e','True','1,000']:
            self.assertIsNone(parse_strict_number(text))
            self.assertIsNone(parse_comparator_number('<'+text))
        ds=TameDataset(pd.DataFrame({'p':['4.2e-28','1E+6']}),[ColumnSpec('p','p',['NUM'])])
        self.assertEqual(validate_dataset(ds).issues,[])
        with tempfile.TemporaryDirectory() as d:
            from tametools.io import write_tame
            path=Path(d)/'small.tame';write_tame(path,ds)
            restored=read_tame(path)
            self.assertEqual(validate_dataset(restored).issues,[])
            self.assertEqual(numeric_series_for(restored,'p').tolist(),[4.2e-28,1e6])

    def test_generic_embedded_chart_rows_are_used_instead_of_unrelated_main_table(self):
        ds=TameDataset(pd.DataFrame({'group':['ALL'],'mean':[999]}),[ColumnSpec(k,k,['STR']) for k in ['group','mean']])
        chart=chart_spec('second',type='interval',x='group',y='mean',rows=[{'group':'female','mean':20,'ci95_low':18,'ci95_high':22}])
        chart.update(Y_LOW='ci95_low',Y_HIGH='ci95_high')
        actual=chart_payloads(with_visualizations(ds,[chart]))[0]['rows'][0]
        self.assertEqual((actual['x'],actual['y'],actual['low'],actual['high']),('female',20,18,22))

    def test_profiles_keep_raw_column_but_avoid_duplicate_sex_category_field(self):
        with TestClient(app) as client:
            ds=dataset_from_payload(client.get('/api/examples/kenya').json(),standardize=False)
        profiles=profile_payloads(ds)
        self.assertEqual(sum(p['column']=='Sex_standard' for p in profiles),1)
        self.assertEqual(sum(p['column']=='Sex' for p in profiles),1)


if __name__=='__main__':unittest.main()
