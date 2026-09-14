import hashlib
from pathlib import Path
import tempfile
import unittest
import pandas as pd
from tametools.bootstrap import init_from_table
from tametools.cellstate import cell_state
from tametools.io import read_tame
from tametools.merge import merge_datasets
from tametools.provenance_audit import verify_history
from tametools.toml_compat import dumps


class InitUnitConversionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
    def tearDown(self): self.tmp.cleanup()
    def build(self, values, config=None, unit='mg/dL', name='input'):
        p=self.root/(name+'.csv'); pd.DataFrame({'Creatinine':values}).to_csv(p,index=False)
        meta={'COLUMN':{'Creatinine':{'TAGS':['RESULT','<NUM>'],'ID':'crea','UNIT':unit}},
              'SETTINGS':{'CRR':'VALUE'}}
        if config is not None: meta['INIT_OPTIONS']={'UNIT_CONVERSIONS':{'Creatinine':config}}
        defs=self.root/(name+'.toml'); defs.write_text(dumps(meta),encoding='utf-8')
        out=self.root/(name+'.tame'); report=init_from_table(p,out,definitions_path=defs)
        return read_tame(out), report, p
    def conversion(self,**kwargs):
        return {'FROM_UNIT':'mg/dL','TO_UNIT':'umol/L','FACTOR':'88.4','REASON':'Reviewed creatinine display-unit bridge',**kwargs}
    def test_comparators_precision_states_raw_and_replay(self):
        values=['1.2','<0.1','1e-3','','<<NULL>>','<<EMPTY>>','<<WS:2>>']
        ds,r,p=self.build(values,self.conversion())
        self.assertEqual(ds.df.Creatinine.tolist()[:3],['106.08','<8.84','0.0884'])
        raw=ds.meta['INIT']['TRANSFORMATIONS'][0]['raw_column']
        self.assertEqual(ds.df[raw].tolist()[:3],values[:3])
        self.assertEqual([cell_state(v) for v in ds.df.Creatinine.iloc[3:]],['ABSENT','NULL','EMPTY','WS'])
        self.assertEqual(ds.column_metadata('Creatinine')['UNIT'],'umol/L')
        self.assertEqual(ds.column_metadata(raw)['UNIT'],'mg/dL')
        self.assertEqual(ds.meta['INIT']['TRANSFORMATIONS'][0]['operation'],'CONVERT_UNIT')
        out=self.root/'replay.tame';init_from_table(p,out,definitions_path=Path(r['decisions_path']))
        pd.testing.assert_frame_equal(ds.df,read_tame(out).df)
        self.assertTrue(all(verify_history(read_tame(out))['checks'][k]=='PASS' for k in ['history','data','controls']))
    def test_unit_definition_alone_does_not_convert_values(self):
        ds,_,_=self.build(['1.2'])
        self.assertEqual(ds.df.Creatinine.tolist(),['1.2'])
        self.assertEqual(list(ds.df.columns),['Creatinine'])
    def test_invalid_declarations_fail_before_output(self):
        for config in [self.conversion(FROM_UNIT='g/L'),self.conversion(FACTOR='-1'),
                       self.conversion(FACTOR='nan'),self.conversion(OFFSET='inf'),
                       self.conversion(REASON=''),self.conversion(TO_UNIT='arbitrary')]:
            with self.subTest(config=config),self.assertRaises(ValueError): self.build(['1.2'],config)
        self.assertFalse((self.root/'input.tame').exists())
    def test_bad_value_does_not_disappear_during_conversion(self):
        with self.assertRaisesRegex(ValueError,'unsupported result'):
            self.build(['1.2','invalid'],self.conversion())
        self.assertFalse((self.root/'input.tame').exists())
    def test_source_scoped_raw_ids_do_not_block_valid_merge(self):
        a,_,_=self.build(['1.2'],self.conversion(),name='source_a')
        b,_,_=self.build(['2.3'],self.conversion(),name='source_b')
        merged=merge_datasets([a,b],source_labels=['A','B']).dataset
        aligned=[c for c in merged.columns if merged.column_metadata(c).get('ID')=='crea']
        self.assertEqual(len(aligned),1)
        self.assertEqual(merged.df[aligned[0].name].tolist(),['106.08','203.32'])
        self.assertEqual(sum(c.has_tag('RAW') for c in merged.columns),2)

if __name__=='__main__': unittest.main()
