"""Regression tests for common-workbench input, EP28, and comparator contracts."""
from pathlib import Path
import sys, tempfile, unittest, zipfile, io, json
from copy import deepcopy
from unittest.mock import patch
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parents[3]
sys.path[:0]=[str(ROOT/'tametools/src'),str(ROOT/'web/backend')]
from tametools.models import TameDataset, ColumnSpec
from tametools.io import read_tame
from app.tametools_bridge import dataset_payload, dataset_from_payload, eda_payload, apply_validation_fix_payload, dataset_to_tame_bytes
from app.workbench_analysis import reference_analysis, bind_reference_input, dependency_message, require_example_dependencies, data_role


def fixture(n=240):
    # Two analytes in long format, independently specified units, original identifiers.
    df=pd.DataFrame({'person':[str(i) for i in range(n)], 'test':['X']*n,
        'value':[str(i+1) for i in range(n)], 'unit':['U/L']*n, 'sex':['0' if i%2==0 else '1' for i in range(n)]})
    tags={'person':['ID','STR'],'test':['TESTNAME','STR'],'value':['RESULT','NUM'],'unit':['UNIT','STR'],'sex':['SEX']}
    columns=[ColumnSpec(k,k,v) for k,v in tags.items()]
    meta={'LOG':[{'OPERATION':'IMPORT','NOTES':'Original import'}]}
    return TameDataset(df,columns,meta)


class WorkbenchAnalysisTests(unittest.TestCase):
    def test_ep28_nonparametric_uses_unit_column_and_type6_without_mutating_input(self):
        ds=fixture(); payload=dataset_payload(ds,filename='long.tame'); before=deepcopy(payload)
        result=reference_analysis(payload,{'OUTLIER_METHOD':'NONE'})
        row=result['analysisTables']['reference_intervals'][0]
        self.assertEqual((row['test_name'],row['unit'],row['n']),('X','U/L',240))
        expected=np.quantile(np.arange(1,241),[.025,.975],method='weibull')
        np.testing.assert_allclose([row['ref_low'],row['ref_high']],expected,atol=1e-12)
        self.assertEqual(payload,before)
        self.assertIn('UNIT_ID',result['metaText'])
        self.assertIn('RI-INPUT-BINDING',result['metaText'])
        self.assertIn('IMPORT',result['metaText'])
        self.assertEqual(result['dataRole'],'result')

    def test_small_samples_explain_absent_limits(self):
        for n,status in [(2,'insufficient_n'),(3,'insufficient_order_resolution')]:
            with self.subTest(n=n):
                result=reference_analysis(dataset_payload(fixture(n)),{'OUTLIER_METHOD':'NONE'})
                row=result['analysisTables']['reference_intervals'][0]
                self.assertEqual(row['status'],status)
                self.assertTrue(row['notes'])
                self.assertIsNone(row['ref_low'])

    def test_parametric_negative_lower_limit_is_not_clamped(self):
        ds=fixture(120);ds.df['value']=['0']*119+['100']
        row=reference_analysis(dataset_payload(ds),{'METHOD':'PARAMETRIC','OUTLIER_METHOD':'NONE'})['analysisTables']['reference_intervals'][0]
        from scipy.stats import norm
        x=np.array([0.]*119+[100.])
        self.assertAlmostEqual(row['ref_low'],x.mean()-norm.ppf(.975)*x.std(ddof=1),places=10)
        self.assertLess(row['ref_low'],0)
        self.assertIn('no automatic zero clamp',row['notes'])

    def test_result_table_is_rejected_as_analysis_input(self):
        payload=dataset_payload(fixture())
        eda=eda_payload(payload)['dataset']
        with self.assertRaisesRegex(ValueError,'분석 결과표'):
            reference_analysis(eda,{})
        ri=reference_analysis(payload,{'OUTLIER_METHOD':'NONE'})
        with self.assertRaisesRegex(ValueError,'분석 결과표'):
            reference_analysis(ri,{})
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'result.tame';path.write_bytes(dataset_to_tame_bytes(dataset_from_payload(ri),d))
            self.assertEqual(data_role(read_tame(path)),'result')
        self.assertIn('IMPORT',eda['metaText'])

    def test_missing_unit_is_actionable_not_guessed(self):
        ds=fixture();ds=ds.replace(columns=[c for c in ds.columns if c.name!='unit'],df=ds.df.drop(columns=['unit']))
        with self.assertRaisesRegex(ValueError,'단위를 확인할 수 없습니다'):
            reference_analysis(dataset_payload(ds),{})

    def test_existing_category_mapping_is_respected_in_sex_partition(self):
        # Use the real bundled contract (0=male, 1=female) to catch global-map regressions.
        ds=read_tame(ROOT/'web/backend/examples/kenya_reference.tame')
        result=reference_analysis(dataset_payload(ds),{'RESULT_IDS':['alt'],'PARTITION_BY':['SEX'],'SEX_ID':'sex','OUTLIER_METHOD':'NONE'})
        rows=result['analysisTables']['reference_intervals']
        female=next(r for r in rows if r['group']!='ALL' and json.loads(r['group']).get('SEX')=='female')
        raw=ds.df.loc[ds.df.Sex.eq('1'),'ALT'].astype(float).to_numpy()
        self.assertEqual(female['n'],len(raw))
        np.testing.assert_allclose([female['ref_low'],female['ref_high']],np.quantile(raw,[.025,.975],method='weibull'))

    def test_valid_comparator_columns_get_policy_without_losing_rows_or_lexemes(self):
        ds=fixture(1277)
        ds.df.loc[:19,'value']='<3'
        ds=ds.replace(columns=[ColumnSpec(c.name,c.name,['RESULT','<NUM>']) if c.name=='value' else c for c in ds.columns])
        original=ds.df['value'].tolist()
        for policy,crr in [('exclude','DELETE'),('value','VALUE')]:
            with self.subTest(policy=policy):
                result=apply_validation_fix_payload(dataset_payload(ds),{'action':'comparator-policy','numComparatorHandling':policy})
                rebuilt=dataset_from_payload(result,standardize=False)
                self.assertEqual(len(rebuilt.df),1277)
                self.assertEqual(rebuilt.df['value'].tolist(),original)
                self.assertEqual(rebuilt.meta['SETTINGS']['CRR'],crr)
                self.assertIn('20',result['operationSummary'])
                self.assertEqual(result['dataRole'],'input')
        with self.assertRaises(ValueError):
            apply_validation_fix_payload(dataset_payload(ds),{'action':'comparator-policy','numComparatorHandling':'delete'})

    def test_dependency_error_lists_all_missing_modules_and_interpreter(self):
        with patch('app.workbench_analysis.importlib.util.find_spec',return_value=None):
            with self.assertRaises(ModuleNotFoundError) as raised:require_example_dependencies()
            message=dependency_message(raised.exception)
        for value in ['pyreadstat','samplics',sys.executable,'-m pip install -e','./tametools[analysis,nhanes,web,report]']:
            self.assertIn(value,message)

    def test_save_does_not_silently_standardize_raw_sex_or_precision(self):
        from fastapi.testclient import TestClient
        from app.main import app
        ds=fixture(3)
        ds.df['sex']=['M','F','Female']
        ds.df['value']=['1.2345678901234567','0.000000000123456789','2']
        payload=dataset_payload(ds)
        with TestClient(app) as client, tempfile.TemporaryDirectory() as d:
            response=client.post('/api/datasets/tame',json=payload)
            self.assertEqual(response.status_code,200)
            p=Path(d)/'saved.tame';p.write_bytes(response.content)
            restored=read_tame(p)
            self.assertEqual(restored.df['sex'].tolist(),ds.df['sex'].tolist())
            self.assertEqual(restored.df['value'].tolist(),ds.df['value'].tolist())
            response=client.post('/api/datasets/xlsx',json=payload)
            self.assertEqual(response.status_code,200)
            import openpyxl
            workbook=openpyxl.load_workbook(io.BytesIO(response.content),read_only=True)
            values=list(workbook['DATA'].values)[1:]
            self.assertEqual([r[4] for r in values],ds.df['sex'].tolist())
            self.assertEqual([r[2] for r in values],ds.df['value'].tolist())
            workbook.close()

    def test_report_bundle_contains_replayable_input_result_and_tables(self):
        from fastapi.testclient import TestClient
        from app.main import app
        with TestClient(app) as client:
            source=client.get('/api/examples/kenya').json()
            self.assertEqual(source['rowCount'],533)
            self.assertEqual(source['issues'],[])
            response=client.post('/api/datasets/reference-interval',json={'dataset':source,'options':{'RESULT_IDS':['alt'],'OUTLIER_METHOD':'NONE'}})
            self.assertEqual(response.status_code,200,response.text[:500])
            result=response.json()
            self.assertEqual(result['dataRole'],'result')
            word=client.get(result['reportFiles'][0]['url'])
            self.assertEqual(word.status_code,200)
            self.assertTrue(word.content.startswith(b'PK'))
            archive=client.get(result['reportFiles'][1]['url'])
            with zipfile.ZipFile(io.BytesIO(archive.content)) as z:
                for name in ['analysis_input.tame','workbench_result.tame','effective_input.json','reference_interval_report_ko.docx']:
                    self.assertIn(name,z.namelist())
                with tempfile.TemporaryDirectory() as d:
                    p=Path(d)/'replay.tame';p.write_bytes(z.read('analysis_input.tame'))
                    replay=reference_analysis(dataset_payload(read_tame(p)),{})
                    a=result['analysisTables']['reference_intervals'];b=replay['analysisTables']['reference_intervals']
                    for x,y in zip(a,b):
                        for key in ['n','ref_low','ref_high','low_ci_low','low_ci_high','high_ci_low','high_ci_high']:
                            self.assertEqual(x[key],y[key],key)
            self.assertEqual(client.get('/api/analysis-reports/invalid/reference_interval_report.zip').status_code,404)
            self.assertEqual(client.get('/api/examples/missing').status_code,404)

if __name__=='__main__':unittest.main()
