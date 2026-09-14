"""Network-free tests for the guided importer and background job failure paths."""
from pathlib import Path
import sys, json, tempfile, threading, time, unittest
from unittest.mock import patch
import pandas as pd
import numpy as np
ROOT=Path(__file__).resolve().parents[3]
sys.path[:0]=[str(ROOT/'tametools/src'),str(ROOT/'web/backend')]
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from app.nhanes_data import prepare_dataset,make_plan,VARIABLES
from app.nhanes_workflow import NhanesService,DownloadSettings,create_router


class GuidedImportTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        (self.root/'source_manifest.json').write_text(json.dumps({'files':{}}))
        self.demo=pd.DataFrame(dict(SEQN=range(1,9),SDDSRVYR=[12]*8,RIDAGEYR=[5,15,25,40,60,80,30,75],
            RIAGENDR=[1,2]*4,SDMVSTRA=[1]*4+[2]*4,SDMVPSU=[1,1,2,2]*2,WTMEC2YR=[9.]*8))
        self.bio=pd.DataFrame({'SEQN':range(2,9),'WTPH2YR':[0.,2.,3.,4.,5.,6.,7.]})
        for v in VARIABLES:self.bio[v['variable']]=[12.]*7
        self.bio['LBXSATSI']=[3/np.sqrt(2)]+[12.]*6

    def tearDown(self):self.temp.cleanup()

    def prepare(self,cycle='2021-2023'):
        with patch('pyreadstat.read_xport',side_effect=[(self.demo,None),(self.bio,None)]):
            return prepare_dataset(self.root,cycle)

    def test_recent_cycle_retains_design_rows_and_uses_phlebotomy_weights(self):
        ds,info=self.prepare()
        self.assertEqual(len(ds.df),8)
        self.assertEqual(ds.df.weight.tolist(),['0','0','2','3','4','5','6','7'])
        self.assertEqual(ds.meta['SURVEY']['EXPECTED_ROWS'],8)
        self.assertEqual(ds.meta['COLUMN']['age']['TOP_CODE_VALUE'],80)
        self.assertEqual(ds.meta['COLUMN']['ALT']['UNIT'],'IU/L')
        self.assertFalse(info['censoring_available'])
        self.assertNotIn('ALT_flag',ds.df)
        self.assertNotIn('CENSORING',ds.meta['COLUMN']['ALT'])

    def test_older_cycle_preserves_released_values_and_flags(self):
        self.demo.SDDSRVYR=10
        self.bio['LBDSATLC']=[1]+[0]*6
        ds,info=self.prepare('2017-2018')
        self.assertTrue(info['censoring_available'])
        self.assertEqual(ds.df.weight.tolist(),['9']*8)
        self.assertEqual(ds.df.ALT.iloc[1],'<3')
        self.assertAlmostEqual(float(ds.df.ALT_released.iloc[1]),3/np.sqrt(2))
        self.assertEqual(ds.meta['COLUMN']['ALT']['UNIT'],'U/L')

    def test_duplicate_id_rejected(self):
        self.bio.loc[1,'SEQN']=self.bio.loc[0,'SEQN']
        with self.assertRaisesRegex(ValueError,'중복'):self.prepare()

    def test_unmatched_laboratory_id_rejected(self):
        self.bio.loc[0,'SEQN']=99
        with self.assertRaisesRegex(ValueError,'연결되지'):self.prepare()

    def test_missing_existing_phlebotomy_weight_not_filled(self):
        self.bio.loc[0,'WTPH2YR']=np.nan
        with self.assertRaisesRegex(ValueError,'결측'):self.prepare()

    def test_wrong_cycle_rejected(self):
        self.demo.SDDSRVYR=10
        with self.assertRaisesRegex(ValueError,'조사 코드'):self.prepare()

    def test_missing_variable_rejected(self):
        del self.bio['LBXSCR']
        with self.assertRaisesRegex(ValueError,'필수 변수'):self.prepare()

    def test_settings_reject_unknown_empty_and_duplicate_variables(self):
        defaults=dict(variables=['ALT'],age_group='adults',by_sex=True,policy='RELEASED')
        for variables in [[],['unknown'],['ALT','ALT']]:
            with self.subTest(variables=variables),self.assertRaises(ValueError):make_plan(dict(defaults,variables=variables))
        with self.assertRaises(ValueError):make_plan(dict(defaults,age_group='80_85'))


class GuidedJobTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.services=[]

    def tearDown(self):
        for service in self.services:service.close()
        self.temp.cleanup()

    def service(self,downloader):
        s=NhanesService(self.temp.name,Path(self.temp.name)/'cache',downloader)
        self.services.append(s)
        return s

    def wait(self,s,id,status):
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            value=s.status(id)
            if value['status']==status:return value
            time.sleep(.02)
        self.fail(str(s.status(id)))

    def test_download_failure_has_retryable_message(self):
        def failed(*a,**kw):raise RuntimeError('download failed: connection timed out')
        s=self.service(failed)
        with self.assertLogs('app.nhanes_workflow',level='ERROR'):
            first=s.start_download(DownloadSettings())['job_id']
            value=self.wait(s,first,'error')
        self.assertIn('인터넷 연결',value['error'])
        self.assertNotIn('cache',value)
        with self.assertLogs('app.nhanes_workflow',level='ERROR'):
            second=s.start_download(DownloadSettings())['job_id']
            self.wait(s,second,'error')
        self.assertNotEqual(first,second)

    def test_cancel_and_duplicate_request(self):
        entered=threading.Event();resume=threading.Event()
        def blocking(*a,**kw):
            entered.set();resume.wait(3)
            kw['progress']('downloaded: DEMO_L.xpt (10 bytes)')
        s=self.service(blocking)
        id=s.start_download(DownloadSettings())['job_id']
        self.assertTrue(entered.wait(2))
        self.assertEqual(id,s.start_download(DownloadSettings())['job_id'])
        s.cancel(id);resume.set()
        self.wait(s,id,'cancelled')
        with self.assertRaises(HTTPException):s.artifact(id,'tame')

    def test_invalid_api_requests_do_not_start_downloads(self):
        app=FastAPI();app.include_router(create_router(self.temp.name))
        with TestClient(app) as client:
            self.assertEqual(client.get('/api/nhanes/catalog').status_code,200)
            self.assertEqual(client.post('/api/nhanes/downloads',json={'cycle':'2020'}).status_code,422)
            self.assertEqual(client.post('/api/nhanes/downloads',json={'url':'https://example.com'}).status_code,422)
            self.assertEqual(client.get('/api/nhanes/jobs/unknown').status_code,404)
            self.assertEqual(client.get('/api/nhanes/jobs/unknown/files/unknown').status_code,404)
            self.assertEqual(client.post('/api/nhanes/analyses',json={'download_id':'unknown'}).status_code,404)

if __name__=='__main__':unittest.main()
