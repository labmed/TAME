"""Integration checks for conflicting sex codes after concatenation and export."""
from copy import deepcopy
from pathlib import Path
import json
import tempfile
import unittest

import numpy as np
import pandas as pd
from fastapi.testclient import TestClient

from tametools.cellstate import NULL, serialize_cell
from tametools.io import read_tame, read_xlsx, write_tame, write_xlsx
from tametools.merge import merge_datasets
from tametools.models import ColumnSpec, TameDataset
from tametools.sex_normalization import normalize_sex_by_source, sex_normalization_preview
from app.main import app
from app.tametools_bridge import dataset_payload, dataset_from_payload, convert_xlsx_selection
from app.workbench_analysis import reference_analysis


def sources(n=6):
    datasets = []
    for label, codes, offset in [('A', ['0','1'], 0), ('B', ['1','2'], 1000)]:
        frame = pd.DataFrame({'Person':[label+str(i) for i in range(n)],
            'Sex':[codes[i % 2] for i in range(n)], 'ALT':[str(offset+i+1) for i in range(n)]})
        columns = [ColumnSpec(k,k,tags) for k,tags in [('Person',['ID','STR']),('Sex',['SEX','STR']),('ALT',['RESULT','NUM'])]]
        meta = {'COLUMN':{'Person':{'ID':'person'}, 'Sex':{'ID':'sex'}, 'ALT':{'ID':'alt','UNIT':'U/L','LABEL':'ALT'}},
                'OBSERVATION':{'VERSION':1,'ROW_UNIT':'person','KEY_IDS':['person'],'SUBJECT_ID':'person','REPEAT_POLICY':'ERROR'}}
        datasets.append(TameDataset(frame,columns,meta))
    return datasets


def merged(n=6):
    return merge_datasets(sources(n), source_labels=['A','B']).dataset


def options():
    return {'sexColumn':'Sex', 'sourceColumn':'source_dataset', 'outputName':'Sex_standard',
            'mappings':[{'source':'A','values':{'0':'female','1':'male'}},
                        {'source':'B','values':{'1':'female','2':'male'}}]}


class SourceSexTests(unittest.TestCase):
    def test_same_code_one_is_male_in_a_female_in_b_and_raw_cells_are_preserved(self):
        ds=merged(); before=deepcopy(ds)
        preview=sex_normalization_preview(ds, {'sexColumn':'Sex','sourceColumn':'source_dataset'})
        self.assertEqual(preview['unmappedCount'],12)
        result=normalize_sex_by_source(ds,options())
        self.assertEqual(result.df.Sex_standard.tolist(), ['female','male']*6)
        for col in ds.df:
            self.assertEqual(result.df[col].tolist(),before.df[col].tolist())
        pd.testing.assert_frame_equal(ds.df,before.df)
        self.assertEqual(ds.meta,before.meta)
        self.assertEqual([c.name for c in result.columns_with_tag('SEX')],['Sex_standard'])
        self.assertEqual(result.meta['RI_EP28']['SEX_ID'],result.column_metadata('Sex_standard')['ID'])
        log=next(x for x in result.meta['LOG'] if x['OPERATION']=='NORMALIZE_SEX_BY_SOURCE')
        self.assertTrue(log['PARAMS']['raw_values_preserved'])

    def test_no_fallback_between_sources_and_unmapped_code_blocks_without_mutation(self):
        ds=merged(); cfg=options(); cfg['mappings'][1]['values'].pop('1')
        with self.assertRaisesRegex(ValueError,'B / 1'):
            normalize_sex_by_source(ds,cfg)
        self.assertNotIn('Sex_standard',ds.df)
        cfg=options(); cfg['sourceColumn']=''
        with self.assertRaisesRegex(ValueError,'출처 열'):
            normalize_sex_by_source(ds,cfg)

    def test_missing_source_blocks_present_sex_and_missing_sex_states_are_preserved(self):
        ds=merged(); ds.df.loc[0,'source_dataset']=None
        with self.assertRaisesRegex(ValueError,'출처 결측'):
            normalize_sex_by_source(ds,options())
        ds=merged(); states=[None,NULL,'',' ']
        for i,value in enumerate(states):ds.df.loc[i,'Sex']=value
        result=normalize_sex_by_source(ds,options())
        self.assertEqual([serialize_cell(x) for x in result.df.Sex_standard[:4]], [serialize_cell(x) for x in states])

    def test_source_labels_remain_distinct_and_code_lexemes_are_canonicalized(self):
        ds=merged(); ds.df['source_dataset']=['01']*6+['1']*6
        ds.df['Sex']=ds.df.Sex.map(lambda s:float(s))
        cfg=options(); cfg['mappings'][0]['source']='01';cfg['mappings'][1]['source']='1'
        self.assertEqual(normalize_sex_by_source(ds,cfg).df.Sex_standard.tolist(),['female','male']*6)
        cfg['mappings'][0]['values']['1.0']='female'
        with self.assertRaisesRegex(ValueError,'동일 코드'):
            normalize_sex_by_source(ds,cfg)

    def test_existing_unrelated_output_and_duplicate_source_labels_are_rejected(self):
        cfg=options();cfg['outputName']='ALT'
        with self.assertRaisesRegex(ValueError,'덮어쓸 수 없습니다'):
            normalize_sex_by_source(merged(),cfg)
        with self.assertRaisesRegex(ValueError,'unique'):
            merge_datasets(sources(),source_labels=['same','same'])

    def test_saved_contract_replays_after_tame_excel_rename_and_reorder(self):
        result=normalize_sex_by_source(merged(),options())
        names={'Sex':'원 코드','source_dataset':'자료 출처','Sex_standard':'분석 성별'}
        meta=deepcopy(result.meta)
        meta['COLUMN']={names.get(k,k):v for k,v in meta['COLUMN'].items()}
        columns=[ColumnSpec(names.get(c.name,c.name),names.get(c.name,c.name),c.tags) for c in reversed(result.columns)]
        result=result.replace(df=result.df.rename(columns=names)[[c.name for c in columns]],columns=columns,meta=meta)
        with tempfile.TemporaryDirectory() as d:
            for suffix,writer,reader in [('tame',write_tame,read_tame),('xlsx',write_xlsx,read_xlsx)]:
                with self.subTest(suffix=suffix):
                    path=Path(d)/('renamed.'+suffix);writer(path,result,standardize_sex_values=False)
                    loaded=reader(path)
                    raw=loaded.df['원 코드'].tolist()
                    replay=normalize_sex_by_source(loaded)
                    self.assertEqual(replay.df['원 코드'].tolist(),raw)
                    self.assertEqual(replay.df['분석 성별'].tolist(),['female','male']*6)
                    self.assertEqual(len(replay.df.columns),len(loaded.df.columns))

    def test_reference_partition_matches_independently_selected_values(self):
        ds=normalize_sex_by_source(merged(240),options())
        result=reference_analysis(dataset_payload(ds), {'PARTITION_BY':['SEX'],'OUTLIER_METHOD':'NONE'})
        rows=result['analysisTables']['reference_intervals']
        for sex,parity in [('female',0),('male',1)]:
            values=np.r_[np.arange(1,241)[parity::2],np.arange(1001,1241)[parity::2]]
            row=next(r for r in rows if r['group']!='ALL' and json.loads(r['group'])['SEX']==sex)
            self.assertEqual(row['n'],240)
            np.testing.assert_allclose([row['ref_low'],row['ref_high']],np.quantile(values,[.025,.975],method='weibull'),atol=1e-12)

    def test_excel_sheet_merge_preview_apply_and_download_roundtrip(self):
        import openpyxl
        with tempfile.TemporaryDirectory() as d, TestClient(app) as client:
            path=Path(d)/'mixed.xlsx';workbook=openpyxl.Workbook();workbook.remove(workbook.active)
            for label,codes in [('A',[0,1,1]),('B',[1,2,1])]:
                sheet=workbook.create_sheet(label);sheet.append(['[[SEX::STR]]Sex','[[RESULT::NUM]]ALT'])
                for i,code in enumerate(codes):sheet.append([code,i+10])
            workbook.save(path);workbook.close()
            ds,_warnings=convert_xlsx_selection(path,sheet_names=['A','B'],mode='merge')
            source=ds.columns_with_tag('SHEET')[0].name
            cfg=options();cfg['sourceColumn']=source
            payload=dataset_payload(ds,filename='mixed.tame')
            response=client.post('/api/datasets/sex-normalization-preview',json={'dataset':payload,'options':cfg})
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['unmappedCount'],0)
            response=client.post('/api/datasets/apply-fix',json={'dataset':payload,'options':{'action':'normalize-sex-by-source','sexNormalization':cfg}})
            self.assertEqual(response.status_code,200,response.text)
            fixed=response.json();restored=dataset_from_payload(fixed,standardize=False)
            self.assertEqual(restored.df.Sex_standard.tolist(),['female','male','male','female','male','female'])
            self.assertEqual(fixed['issues'],[])
            raw=client.post('/api/datasets/tame',json=fixed);p=Path(d)/'saved.tame';p.write_bytes(raw.content)
            self.assertEqual(normalize_sex_by_source(read_tame(p)).df.Sex_standard.tolist(),restored.df.Sex_standard.tolist())


if __name__=='__main__':unittest.main()
