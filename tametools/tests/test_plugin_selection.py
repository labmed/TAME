"""File-specific choices across direct, composed and saved plugin runs."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
import threading
import pandas as pd
import pytest

from tametools.io import read_tame, write_tame
from tametools.models import OperationOutput
from tametools.plugin_base import list_plugins, run_plugin
from tametools.plugin_base.base import PluginSpec
from tametools.plugin_base import manager
from tametools.plugin_base.selection import selected_plugin_names, with_plugin_selection, plugin_selection_state
from tametools.provenance_audit import verify_history
from tametools.pipeline import execute_work

ROOT = Path(__file__).resolve().parents[2]

@pytest.fixture
def source():
    ds = read_tame(ROOT / 'web/backend/examples/qc_workflow.tame')
    ds.meta.pop('PLUGINS', None)
    return ds

def select(source, names):
    return source.replace(meta=with_plugin_selection(source.meta, names))

def test_legacy_empty_and_normalized_selection(source):
    original = deepcopy(source.meta)
    assert selected_plugin_names(source.meta) is None
    assert len(run_plugin(source, 'QC_ANALYSIS', source.meta, 'qc', {'MODE':'PRECISION'}).table) == 8
    disabled = select(source, [])
    with pytest.raises(ValueError, match='선택하지 않은.*QC_ANALYSIS'):
        run_plugin(disabled, 'QC_ANALYSIS', {}, 'qc', {})
    allowed = select(source, [' qc-analysis ', 'QC_ANALYSIS'])
    assert allowed.meta['PLUGINS']['ENABLED'] == ['QC_ANALYSIS']
    assert len(run_plugin(allowed, 'qc-analysis', allowed.meta, 'qc', {'MODE':'PRECISION'}).table) == 8
    assert source.meta == original

@pytest.mark.parametrize('value', ['QC_ANALYSIS', None, {}, [1], [''], ['   ']])
def test_malformed_choice_cannot_silently_enable_all(value):
    with pytest.raises(ValueError, match='list of plugin names'):
        selected_plugin_names({'PLUGINS': {'ENABLED': value}})

def test_case_insensitive_setting_preserves_imports():
    meta = {'plugins': {'modules': ['local.module'], 'enabled': []}, 'QC_ANALYSIS': {'MODE':'PRECISION'}}
    selected = with_plugin_selection(meta, ['qc_analysis'])
    assert selected['PLUGINS'] == {'modules':['local.module'], 'ENABLED':['QC_ANALYSIS']}
    reset = with_plugin_selection(selected, None)
    assert selected_plugin_names(reset) is None
    assert reset['PLUGINS']['modules'] == ['local.module']


def test_workflow_dependencies_saved_and_auditable(source, tmp_path):
    chosen = select(source, ['QC_WORKFLOW'])
    out = run_plugin(chosen, 'QC_WORKFLOW', chosen.meta, 'QC_WORKFLOW', chosen.meta['QC_WORKFLOW'])
    assert len(out.tables['rules.main']) == 48 and len(out.tables['review.main']) == 24
    assert out.dataset.meta['PLUGINS']['ENABLED'] == ['QC_WORKFLOW']
    filename = tmp_path/'result.tame'; write_tame(filename, out.dataset, standardize_sex_values=False)
    reloaded = read_tame(filename)
    assert reloaded.meta['PLUGINS']['ENABLED'] == ['QC_WORKFLOW']
    checks = verify_history(reloaded)['checks']
    assert all(checks[k] == 'PASS' for k in ('history','data','controls')), checks
    with pytest.raises(ValueError, match='선택하지 않은.*CHEMISTRY_ANALYSIS'):
        run_plugin(reloaded, 'CHEMISTRY_ANALYSIS', reloaded.meta, 'unselected', {})


def test_work_pipeline_cannot_bypass_file_choice(source):
    chosen = select(source, ['QC_ANALYSIS'])
    chosen.meta['WORKS'] = {'DEFAULT':['QC_ANALYSIS']}
    chosen.meta['QC_ANALYSIS'] = {'MODE':'PRECISION'}
    out = execute_work(chosen)
    assert len(out.final_dataset.df) == 8
    chosen.meta['WORKS']['DEFAULT'] = ['RESULT_TREND']
    with pytest.raises(ValueError, match='선택하지 않은.*RESULT_TREND'):
        execute_work(chosen)


def test_alias_selection_uses_same_handler(source, monkeypatch):
    list_plugins()
    handler = lambda ds, meta, step, opts: OperationOutput(step, dataset=ds)
    monkeypatch.setattr(manager, '_TRUSTED_PLUGINS', {n:PluginSpec(n, '', handler) for n in ('SHORT','LONG')})
    chosen = select(source, ['LONG'])
    assert run_plugin(chosen, 'SHORT', chosen.meta, 'alias', {}).dataset is not None


def test_dependency_preflight_and_nested_policy(source, monkeypatch):
    list_plugins()
    calls = []
    def child(ds, meta, step, opts):
        calls.append('child')
        return OperationOutput(step, dataset=ds)
    def parent(ds, meta, step, opts):
        fresh = ds.replace(meta={})
        run_plugin(fresh, opts.get('call', 'CHILD'), {}, 'child', {})
        return OperationOutput(step, dataset=fresh)
    specs = {'PARENT':PluginSpec('PARENT','',parent,dependencies=('CHILD',)),
             'CHILD':PluginSpec('CHILD','',child), 'OTHER':PluginSpec('OTHER','',lambda *a: child(*a))}
    monkeypatch.setattr(manager, '_TRUSTED_PLUGINS', specs)
    chosen = select(source, ['PARENT'])
    out = run_plugin(chosen, 'PARENT', chosen.meta, 'parent', {})
    assert calls == ['child'] and out.dataset.meta['PLUGINS']['ENABLED'] == ['PARENT']
    with pytest.raises(ValueError, match='선택하지 않은.*OTHER'):
        run_plugin(chosen, 'PARENT', chosen.meta, 'parent', {'call':'OTHER'})
    assert manager._SELECTION_CONTEXT.get() is None
    specs.pop('CHILD')
    with pytest.raises(ValueError, match='dependencies unavailable.*CHILD'):
        run_plugin(chosen, 'PARENT', chosen.meta, 'parent', {})
    assert calls == ['child']
    specs['CHILD'] = PluginSpec('CHILD','',child,dependencies=('PARENT',))
    with pytest.raises(ValueError, match='dependency cycle'):
        run_plugin(chosen, 'PARENT', chosen.meta, 'parent', {})


def test_simultaneous_files_keep_independent_choices(source, monkeypatch):
    list_plugins(); barrier = threading.Barrier(2)
    def parent(ds, meta, step, opts):
        barrier.wait(timeout=10)
        return run_plugin(ds.replace(meta={}), opts['child'], {}, step, {})
    def a(ds, meta, step, opts): return OperationOutput(step, dataset=ds, message='a')
    def b(ds, meta, step, opts): return OperationOutput(step, dataset=ds, message='b')
    specs = {'P':PluginSpec('P','',parent), 'A':PluginSpec('A','',a), 'B':PluginSpec('B','',b)}
    monkeypatch.setattr(manager, '_TRUSTED_PLUGINS', specs)
    def run(name):
        ds = select(source, ['P', name])
        return run_plugin(ds, 'P', ds.meta, name, {'child':name})
    with ThreadPoolExecutor(max_workers=2) as pool:
        outputs = list(pool.map(run, ['A','B']))
    assert [out.message for out in outputs] == ['a','b']
    assert [out.dataset.meta['PLUGINS']['ENABLED'] for out in outputs] == [['P','A'],['P','B']]
    assert manager._SELECTION_CONTEXT.get() is None


def test_unavailable_names_remain_portable_without_enabling_others(source):
    meta = with_plugin_selection(source.meta, ['NOT_INSTALLED'])
    state = plugin_selection_state(meta, list_plugins())
    assert state['missing'] == ['NOT_INSTALLED'] and state['warnings']
    assert 'QC_ANALYSIS' not in state['effective']


def test_cli_lists_and_runs_only_file_plugins(source, tmp_path, monkeypatch, capsys):
    import sys
    from tametools.cli import main
    chosen = select(source, ['QC_ANALYSIS'])
    path = tmp_path/'selected.tame'; write_tame(path, chosen, standardize_sex_values=False)
    monkeypatch.setattr(sys, 'argv', ['tametools','plugins', str(path)])
    assert main() == 0
    listing = capsys.readouterr().out
    assert 'QC_ANALYSIS:' in listing and 'RI_EP28:' not in listing
    monkeypatch.setattr(sys, 'argv', ['tametools','run-plugin', str(path), 'QC_ANALYSIS'])
    assert main() == 0
    capsys.readouterr()
    monkeypatch.setattr(sys, 'argv', ['tametools','run-plugin', str(path), 'RESULT_TREND'])
    assert main() == 1
    assert '선택하지 않은' in capsys.readouterr().err


def test_standalone_workflow_keeps_selection_after_table_only_output(source, monkeypatch):
    from tametools.plugin_workflow import PluginWorkflow
    list_plugins()
    def table_only(ds, meta, step, opts):
        return OperationOutput(step, table=pd.DataFrame({'value':[1, 2]}))
    monkeypatch.setattr(manager, '_TRUSTED_PLUGINS', {'TABLE_ONLY':PluginSpec('TABLE_ONLY','',table_only)})
    flow = PluginWorkflow(select(source, ['TABLE_ONLY']))
    out = flow.plugin('first', 'TABLE_ONLY')
    assert out.dataset.meta['PLUGINS']['ENABLED'] == ['TABLE_ONLY']
    with pytest.raises(ValueError, match='선택하지 않은.*OTHER'):
        flow.plugin('second', 'OTHER', source='first')
