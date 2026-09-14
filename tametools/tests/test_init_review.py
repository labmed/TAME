import hashlib,json,tempfile,unittest
from pathlib import Path
import pandas as pd
from tametools.bootstrap import init_from_table,infer_column_tags,infer_narrow_type
from tametools.categories import normalize_categories,category_validation_issues
from tametools.io import read_tame
from tametools.analysis import describe_dataset

SCOPED='''
[COLUMN.Sex]
TAGS=["SEX","CATEGORY","SEX_CODES"]
[CATEGORIES.SEX_CODES]
VALUES=["male","female"]
STRICT=true
SOURCE_COLUMN="source"
MAP={"1"="female"}
[CATEGORIES.SEX_CODES.SOURCE_MAPS.A]
"0"="male"
"1"="female"
[CATEGORIES.SEX_CODES.SOURCE_MAPS.B]
"1"="male"
"2"="female"
'''

class InitReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
    def tearDown(self):self.tmp.cleanup()
    def build(self,frame,definitions=None):
        p=self.root/'input.csv';frame.to_csv(p,index=False)
        defs=None
        if definitions:
            defs=self.root/'reviewed.toml';defs.write_text(definitions,encoding='utf-8')
        out=self.root/'data.tame'
        result=init_from_table(p,out,definitions_path=defs)
        return read_tame(out),result
    def test_source_dependent_conflicting_code_and_unknown_source(self):
        ds,r=self.build(pd.DataFrame({'source':['A','A','B','B','C'],'Sex':['0','1','1','2','1']}),SCOPED)
        out,report=normalize_categories(ds)
        self.assertEqual(out.df.Sex.tolist(),['male','female','male','female','1'])
        self.assertEqual(int(report.unmapped_cells.sum()),1)
        self.assertEqual(len(category_validation_issues(ds)),1)
        self.assertEqual(next(i for i in r['review']['issues'] if i['code']=='SEX_CODE_MAP')['status'],'NEEDS_DEFINITION')
    def test_scoped_mapping_resolves_only_after_all_observations_are_covered(self):
        ds,r=self.build(pd.DataFrame({'source':['A','A','B','B'],'Sex':['0','1','1','2']}),SCOPED)
        self.assertEqual(r['review']['required_definitions'],0)
        self.assertEqual(ds.df.Sex.tolist(),['0','1','1','2'])
        out,report=normalize_categories(ds)
        self.assertEqual(out.df.Sex.value_counts().to_dict(),{'male':2,'female':2})
        ds.df.index=[0,0,0,0]
        out,report=normalize_categories(ds)
        self.assertEqual(out.df.Sex.tolist(),['male','female','male','female'])
        self.assertEqual(category_validation_issues(ds),[])
    def test_unknown_source_column_cannot_silently_use_global_map(self):
        with self.assertRaisesRegex(ValueError,'source column'):
            self.build(pd.DataFrame({'Sex':['1','2']}),SCOPED)
    def test_unmapped_numeric_sex_is_reported_with_source_counts(self):
        ds,r=self.build(pd.DataFrame({'source':['A','B'],'Sex':['1','1']}))
        issue=next(i for i in r['review']['issues'] if i['code']=='SEX_CODE_MAP')
        self.assertEqual(issue['source_counts'],{'A':{'1':1},'B':{'1':1}})
        self.assertEqual(ds.df.Sex.tolist(),['1','1'])
    def test_comparator_default_is_exposed_and_explicit_selection_resolves_it(self):
        frame=pd.DataFrame({'hsCRP':['<0.1','2']})
        _,r=self.build(frame)
        self.assertEqual(r['review']['required_definitions'],1)
        ds,r=self.build(frame,'[SETTINGS]\nCRR="DELETE"')
        self.assertEqual(r['review']['required_definitions'],0)
        self.assertEqual(float(describe_dataset(ds).iloc[0]['mean']),2)
        ds,r=self.build(frame,'[SETTINGS]\ncrr="DELETE"')
        self.assertEqual(ds.settings()['CRR'],'DELETE')
        self.assertEqual(float(describe_dataset(ds).iloc[0]['mean']),2)
    def test_age_delimiters_and_scientific_notation(self):
        self.assertEqual(infer_column_tags('test_name',pd.Series(['ALT','AST'])),('TESTNAME',))
        self.assertEqual(infer_column_tags('Age_with_unit',pd.Series(['7mo','12mo'])),('AGE',))
        self.assertEqual(infer_column_tags('PatientAge',pd.Series(['7mo','12mo'])),('AGE',))
        self.assertEqual(infer_narrow_type(pd.Series(['1e-3','<2E-3'])), '<NUM>')
        ds,_=self.build(pd.DataFrame({'Age_with_unit':['6mo','12mo']}))
        self.assertAlmostEqual(float(describe_dataset(ds).iloc[0]['mean']),0.75)
    def test_literal_na_and_quoted_cell_content_survive_init(self):
        ds,r=self.build(pd.DataFrame({'comment':['NA','a\tb\nc'],'PatientID':['001','002']}))
        self.assertEqual(ds.df.comment.tolist(),['NA','a\tb\nc'])
        self.assertEqual(ds.df.PatientID.tolist(),['001','002'])
        self.assertEqual(r['review']['source_sha256'],hashlib.sha256((self.root/'input.csv').read_bytes()).hexdigest())
    def test_existing_definition_template_is_preserved(self):
        self.build(pd.DataFrame({'Sex':['0','1']}))
        template=self.root/'data.definitions.toml';template.write_text('# Edited by user',encoding='utf-8')
        self.build(pd.DataFrame({'Sex':['0','1']}))
        self.assertEqual(template.read_text(),'# Edited by user')
    def test_template_placeholders_are_not_executable_definitions(self):
        with self.assertRaisesRegex(ValueError,'placeholders'):
            self.build(pd.DataFrame({'Sex':['0','1']}),'[COLUMN.Sex]\nID="DEFINE_ANALYTE_ID"')

if __name__=='__main__':unittest.main()
