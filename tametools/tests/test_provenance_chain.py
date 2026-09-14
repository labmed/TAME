from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

import pandas as pd
from tametools.models import TameDataset, ColumnSpec
from tametools.provenance import append_log_entry, log_entries, inherit_logs, operation_scope, restore_log_values
from tametools.provenance_audit import verify_history, data_fingerprint, result_fingerprint
from tametools.pipeline import execute_work
from tametools.command_pipelines import execute_command_pipeline
from tametools.merge import merge_datasets
from tametools.io import read_tame, write_tame, read_xlsx, write_xlsx
from tametools.toml_compat import dumps, loads


def example():
    return TameDataset(pd.DataFrame({'id': [str(i) for i in range(60)], 'value': [str(i+1) for i in range(60)]}),
        [ColumnSpec('id', 'id', ['ID', 'STR']), ColumnSpec('value', 'value', ['RESULT', 'NUM'])],
        {'ANALYSIS_CONTRACT': {'VERSION': 1}, 'COLUMN': {'id': {'ID': 'person'}, 'value': {'ID': 'value', 'UNIT': 'U/L'}},
         'OBSERVATION': {'VERSION': 1, 'ROW_UNIT': 'person', 'KEY_IDS': ['person'], 'SUBJECT_ID': 'person', 'REPEAT_POLICY': 'ERROR'}})


class ProvenanceChainTests(unittest.TestCase):
    def assert_current(self, ds):
        checks = verify_history(ds)
        self.assertEqual(checks['errors'], [])
        self.assertEqual(checks['checks']['data'], 'PASS')
        self.assertEqual(checks['checks']['controls'], 'PASS')
        return checks

    def test_work_and_command_chains_keep_all_steps(self):
        for style in ['work', 'command']:
            ds = example()
            ds.meta.update(WORKS={'DEFAULT': ['SAMPLE', 'RI_EP28']}, SAMPLE={'ROWS': 40, 'SEED': 42},
                RI_EP28={'RESULT_IDS': ['value'], 'CI_METHOD': 'RANK', 'PLOT_MAX_GROUPS': 0},
                PIPELINES={'DEFAULT': ['sample --rows 40 --seed 42', 'describe']})
            ds = append_log_entry(ds, action='IMPORT')
            out = execute_work(ds) if style == 'work' else execute_command_pipeline(ds)
            operations = [e['OPERATION'] for e in log_entries(out.final_dataset)]
            self.assertIn('IMPORT', operations)
            self.assertIn('WORK:SAMPLE' if style == 'work' else 'COMMAND:SAMPLE', operations)
            self.assert_current(out.final_dataset)
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp)/'result.tame'
                write_tame(path, out.final_dataset, standardize_sex_values=False)
                self.assert_current(read_tame(path))

    def test_multiple_parents_and_shared_ancestry(self):
        origin = append_log_entry(example(), action='IMPORT')
        a = append_log_entry(origin.replace(df=origin.df.iloc[:2].copy()), action='A', input_dataset=origin)
        b = append_log_entry(origin.replace(df=origin.df.iloc[2:4].copy()), action='B', input_dataset=origin)
        merged = merge_datasets([a,b], source_labels=['A','B']).dataset
        entries = log_entries(merged)
        self.assertEqual([e['OPERATION'] for e in entries], ['IMPORT','A','B','MERGE'])
        self.assertEqual(set(entries[-1]['PARENT_EVENT_IDS']), {entries[1]['EVENT_ID'], entries[2]['EVENT_ID']})
        self.assert_current(merged)

    def test_saved_log_detects_data_controls_history_and_context_changes(self):
        base = append_log_entry(example(), action='IMPORT')
        with tempfile.TemporaryDirectory() as tmp:
            for suffix, writer, reader in [('tame',write_tame,read_tame), ('xlsx',write_xlsx,read_xlsx)]:
                path=Path(tmp)/('test.'+suffix)
                writer(path,base)
                loaded=reader(path)
                self.assert_current(loaded)
                changed=loaded.replace(df=loaded.df.copy(),meta=deepcopy(loaded.meta))
                changed.df.loc[0,'value']='999'
                self.assertEqual(verify_history(changed)['checks']['data'],'FAIL')
                changed=loaded.replace(meta=deepcopy(loaded.meta))
                changed.meta['COLUMN']['value']['UNIT']='mg/L'
                self.assertEqual(verify_history(changed)['checks']['controls'],'FAIL')
                changed=loaded.replace(meta=deepcopy(loaded.meta))
                changed.meta['LOG'][0]['SUMMARY']='edited'
                self.assertEqual(verify_history(changed)['checks']['history'],'FAIL')
                changed=loaded.replace(meta=deepcopy(loaded.meta))
                run=changed.meta['LOG'][0]['RUN_ID']
                changed.meta['PROVENANCE']['CONTEXTS'][run]['TOOL_VERSION']='changed'
                self.assertEqual(verify_history(changed)['checks']['history'],'FAIL')

    def test_generated_seed_can_reproduce_rows(self):
        ds=example();ds.meta.update(WORKS={'DEFAULT':['SAMPLE']},SAMPLE={'ROWS':10})
        out=execute_work(ds).final_dataset
        params=restore_log_values(log_entries(out)[-1]['EFFECTIVE_PARAMS'])
        self.assertIsInstance(params['SEED'],int)
        ds.meta['SAMPLE']['SEED']=params['SEED']
        again=execute_work(ds).final_dataset
        self.assertEqual(data_fingerprint(out),data_fingerprint(again))

    def test_legacy_extensions_null_and_empty_survive(self):
        ds=example();ds.meta['log']={'ENTRIES':[{'ACTION':'OLD','TIME':'2000','OPERATOR':'A','CUSTOM':{'x':1}}]}
        out=append_log_entry(ds,action='NEXT',parameters={'null':None,'empty':'','mixed':[None,1,{'a':True}]})
        meta=loads(dumps(out.meta))
        self.assertEqual(meta['LOG'][0]['OPERATOR'],'A')
        self.assertNotIn('STATUS',meta['LOG'][0])
        params=restore_log_values(meta['LOG'][1]['PARAMS'])
        self.assertIsNone(params['null']);self.assertEqual(params['empty'],'')
        self.assertEqual(params['mixed'],[None,1,{'a':True}])
        self.assertEqual(verify_history(out)['checks']['history'],'PARTIAL')

    def test_skipped_and_failed_steps_are_not_successful_outputs(self):
        ds=example();ds.meta['WORKS']={'DEFAULT':['NOT_A_STEP']}
        skipped=execute_work(ds).final_dataset
        self.assertEqual(log_entries(skipped)[-1]['STATUS'],'SKIPPED')
        self.assertEqual(log_entries(skipped)[-1]['OUTPUTS'],[])
        self.assertEqual(len(verify_history(skipped)['unsuccessful_steps']),1)
        ds.meta.update(WORKS={'DEFAULT':['SAMPLE','RI_EP28']},SAMPLE={'ROWS':10,'SEED':42},RI_EP28={'ROWS':20})
        with self.assertRaises(ValueError) as caught:
            execute_work(ds)
        failed=caught.exception.audit_dataset
        self.assertEqual(len(failed.df),10)
        self.assertEqual([e['STATUS'] for e in log_entries(failed)],['SUCCEEDED','FAILED'])
        self.assertEqual(log_entries(failed)[-1]['OUTPUTS'],[])

    def test_removing_middle_event_is_detected(self):
        ds=example();ds.meta['WORKS']={'DEFAULT':['DESCRIBE','EDA','DESCRIBE']}
        out=execute_work(ds).final_dataset
        out.meta['LOG'].pop(1)
        self.assertEqual(verify_history(out)['checks']['history'],'FAIL')

    def test_analysis_table_changes_are_compared(self):
        ds=example();ds.meta['WORKS']={'DEFAULT':['ANALYZE']}
        ds.meta['ANALYSIS_PLAN']={'VERSION':1,'MODE':'SAMPLE','POLICIES':['VALUE'], 'PRIMARY_POLICY':'VALUE',
                                  'RESULT_IDS':['value'],'HISTOGRAM_BINS':10}
        first=execute_work(ds)
        ds.meta['ANALYSIS_PLAN']['HISTOGRAM_BINS']=20
        second=execute_work(ds)
        self.assertEqual(data_fingerprint(first.final_dataset),data_fingerprint(second.final_dataset))
        self.assertNotEqual(result_fingerprint(first),result_fingerprint(second))

    def test_malformed_history_is_reported_and_input_gaps_survive(self):
        ds=append_log_entry(example(),action='IMPORT')
        malformed=ds.replace(meta=deepcopy(ds.meta))
        malformed.meta['PROVENANCE']['CONTEXTS']='invalid'
        self.assertEqual(verify_history(malformed)['checks']['history'],'FAIL')
        edited=ds.replace(df=ds.df.copy())
        edited.df.loc[0,'value']='999'
        result=append_log_entry(edited,action='NEXT',input_dataset=edited)
        report=verify_history(result)
        self.assertEqual(report['checks']['history'],'PARTIAL')
        self.assertTrue(report['missing'])

    def test_precision_and_large_snapshot_survive_excel(self):
        ds=example()
        ds.df.loc[0,'value']='1.123456789012345678901234567890123456789'
        first=data_fingerprint(ds)
        changed=ds.replace(df=ds.df.copy())
        changed.df.loc[0,'value']='1.123456789012345678901234567890123456788'
        self.assertNotEqual(first,data_fingerprint(changed))
        ds.meta['DOCUMENTATION']={f'part_{i}':'context '*1000 for i in range(8)}
        ds=append_log_entry(ds,action='IMPORT')
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'large.tame';write_tame(path,ds)
            self.assert_current(read_tame(path))
            path=Path(tmp)/'large.xlsx';write_xlsx(path,ds)
            self.assert_current(read_xlsx(path))
        # Full snapshots are chunked; no snapshot chunk exceeds Excel's text limit.
        self.assertTrue(all(len(chunk['TEXT'])<=4000 for value in ds.meta['PROVENANCE']['CONTROLS'].values() for chunk in value['CHUNKS']))

    def test_cli_default_chain_and_scoped_checks(self):
        import contextlib,io,sys
        from tametools import cli
        ds=example();ds.meta.update(WORKS={'DEFAULT':['SAMPLE','DESCRIBE']},SAMPLE={'ROWS':10,'SEED':42})
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'source.tame';target=Path(tmp)/'target.tame';write_tame(source,ds)
            def run(*args):
                old=sys.argv;sys.argv=['tametools',*map(str,args)];stream=io.StringIO()
                try:
                    with contextlib.redirect_stdout(stream): code=cli.main()
                    return code,stream.getvalue()
                finally:sys.argv=old
            code,text=run('run',source,'--output',target)
            self.assertEqual(code,0,text)
            self.assertIn('WORK:SAMPLE',[e['OPERATION'] for e in read_tame(target).meta['LOG']])
            self.assertEqual(run('audit',target)[0],0)
            self.assertEqual(run('verify',source)[0],0)
            ds.meta['WORKS']={'DEFAULT':['NOT_A_STEP']};write_tame(source,ds)
            code,text=run('verify',source)
            self.assertEqual(code,1,text)
            self.assertIn('unsuccessful steps',text)


if __name__=='__main__': unittest.main()
