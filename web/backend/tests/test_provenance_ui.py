from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient
from app.main import app
from app.tametools_bridge import dataset_from_payload
from tametools.io import read_tame, read_xlsx
from tametools.provenance_audit import verify_history


class ProvenanceUiTests(unittest.TestCase):
    def setUp(self):
        self.client=TestClient(app)

    def test_excel_merge_normalize_and_reopen_ledger(self):
        path=Path(__file__).resolve().parents[1]/'examples/sex_coding_sources.xlsx'
        upload=self.client.post('/api/files/open',files={'file':(path.name,path.read_bytes())}).json()
        source=self.client.post('/api/xlsx/convert',json={'workbookId':upload['workbookId'],'mode':'merge','sheets':['A_0_1','B_1_2']}).json()
        self.assertEqual(source['rowCount'],6)
        events=[e for g in source['provenance']['groups'] for e in g['events']]
        self.assertEqual([e['OPERATION'] for e in events].count('IMPORT_XLSX'),2)
        self.assertIn('MERGE',[e['OPERATION'] for e in events])
        mapping=json.loads((path.parent/'sex_coding_mapping.json').read_text())
        response=self.client.post('/api/datasets/apply-fix',json={'dataset':source,'options':{'action':'normalize-sex-by-source','sexNormalization':mapping}})
        self.assertEqual(response.status_code,200,response.text[:600])
        fixed=response.json()
        groups=fixed['provenance']['groups']
        self.assertEqual(groups[-1]['label'],'출처별 성별 정규화')
        self.assertEqual(len(groups[-1]['events']),2)
        self.assertEqual(groups[-1]['counts']['MAPPED_ROWS'],6)
        self.assertEqual(len(groups[-1]['mappings']),2)
        self.assertEqual(fixed['provenance']['verification']['checks']['history'],'PASS')
        for extension,reader in [('tame',read_tame),('xlsx',read_xlsx)]:
            saved=self.client.post('/api/datasets/'+extension,json=fixed)
            self.assertEqual(saved.status_code,200,saved.text[:600] if extension=='tame' else '')
            with tempfile.TemporaryDirectory() as tmp:
                p=Path(tmp)/('result.'+extension);p.write_bytes(saved.content)
                loaded=reader(p);report=verify_history(loaded)
                self.assertEqual(report['checks']['history'],'PASS',report)
                self.assertEqual(report['checks']['data'],'PASS',report)
                self.assertEqual(report['checks']['controls'],'PASS',report)

    def test_current_edits_checked_and_committed(self):
        original=self.client.get('/api/examples/kenya').json()
        original=self.client.post('/api/datasets/apply-fix',json={'dataset':original,'options':{'action':'comparator-policy','numComparatorHandling':'value'}}).json()
        edited=deepcopy(original)
        edited['dataRows'][0][4]='999'
        check=self.client.post('/api/datasets/provenance',json=edited).json()
        self.assertEqual(check['verification']['checks']['data'],'FAIL')
        saved=self.client.post('/api/datasets/tame',json=edited)
        self.assertEqual(saved.status_code,200,saved.text[:500])
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'edited.tame';path.write_bytes(saved.content);dataset=read_tame(path)
            self.assertIn('MANUAL_EDIT',[e['OPERATION'] for e in dataset.meta['LOG']])
            self.assertEqual(verify_history(dataset)['checks']['data'],'PASS')

    def test_failed_analysis_has_downloadable_record(self):
        source=self.client.get('/api/examples/kenya').json()
        response=self.client.post('/api/datasets/reference-interval',json={'dataset':source,'options':{'ROWS':20}})
        self.assertEqual(response.status_code,400)
        file=response.json()['auditFile']
        record=self.client.get(file['url']).json()
        self.assertEqual(record['LOG'][-1]['STATUS'],'FAILED')
        self.assertEqual(record['LOG'][-1]['OUTPUTS'],[])
        self.assertIn('Unknown RI_EP28 option',record['LOG'][-1]['ERROR']['MESSAGE'])
        self.assertNotIn('DATA',record)

    def test_kenya_artifacts_and_saved_history(self):
        source=self.client.get('/api/examples/kenya').json()
        response=self.client.post('/api/datasets/reference-interval',json={'dataset':source,'options':{'RESULT_IDS':['alt'],'OUTLIER_METHOD':'NONE'}})
        self.assertEqual(response.status_code,200,response.text[:500])
        result=response.json()
        checked=self.client.post('/api/datasets/provenance',json=result).json()['verification']
        self.assertEqual(checked['errors'],[],checked)
        self.assertEqual(checked['checks']['data'],'PASS',checked)
        self.assertEqual(checked['checks']['controls'],'PASS',checked)
        self.assertEqual(checked['checks']['artifacts'],'PASS',checked)
        exported=self.client.post('/api/datasets/provenance/export',json=result).json()
        self.assertIn('CONTROLS',exported['PROVENANCE'])
        self.assertEqual(exported['LOG'][-1]['OPERATION'],'REFERENCE-INTERVAL')


if __name__=='__main__': unittest.main()
