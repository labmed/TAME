"""Select plugins without executing them, and preserve choices in downloaded TAME."""
from pathlib import Path
import io
import pytest
from fastapi.testclient import TestClient
from tametools.io import read_tame
from tametools.provenance import log_entries
from tametools.provenance_audit import verify_history
from app.main import app
from app.tametools_bridge import (dataset_payload, dataset_from_payload, dataset_to_tame_bytes,
    run_meta_analysis_payload, run_web_plugin_payload, select_plugins_payload)

ROOT = Path(__file__).resolve().parents[3]

@pytest.fixture
def payload():
    source = read_tame(ROOT/'web/backend/examples/qc_workflow.tame')
    source.meta.pop('PLUGINS', None)
    source.meta['ANALYSES'] = {'QC': {'PLUGIN':'QC_ANALYSIS'}, 'RI': {'PLUGIN':'RI_EP28'}}
    return dataset_payload(source, filename='qc.tame')


def test_selection_api_and_roundtrip_preserve_data_and_audit(payload, tmp_path):
    with TestClient(app) as client:
        response = client.post('/api/plugins/selection', json={'dataset':payload,'options':{'enabled':['QC_WORKFLOW']}})
        assert response.status_code == 200, response.text
        result = response.json()
        assert result['dataRows'] == payload['dataRows']
        assert result['filename'] == payload['filename']
        assert {p['name'] for p in result['plugins']} == {'QC_WORKFLOW','QC_ANALYSIS','QC_RUN_REVIEW','RESULT_TREND'}
        assert len(result['pluginCatalog']) > len(result['plugins'])
        assert result['analyses'][0]['name'] == 'QC' and len(result['analyses']) == 1
        assert result['pluginSelection']['enabled'] == ['QC_WORKFLOW']
        dataset = dataset_from_payload(result, standardize=False)
        assert log_entries(dataset)[-1]['OPERATION'] == 'PLUGIN_SELECTION'
        path = tmp_path/'chosen.tame'; path.write_bytes(dataset_to_tame_bytes(dataset, tmp_path))
        saved = read_tame(path)
        assert saved.meta['PLUGINS']['ENABLED'] == ['QC_WORKFLOW']
        checks = verify_history(saved)['checks']
        assert all(checks[k] == 'PASS' for k in ('history','data','controls')), checks
        empty = client.post('/api/plugins/selection', json={'dataset':result,'options':{'enabled':[]}}).json()
        assert empty['plugins'] == [] and empty['analyses'] == []
        reset = client.post('/api/plugins/selection', json={'dataset':empty,'options':{'enabled':None}}).json()
        assert reset['pluginSelection']['mode'] == 'all' and len(reset['plugins']) == len(reset['pluginCatalog'])


def test_all_analysis_routes_enforce_same_choice(payload, tmp_path):
    disabled = select_plugins_payload(payload, {'enabled':[]})
    with pytest.raises(ValueError, match='선택하지 않은.*QC_ANALYSIS'):
        run_web_plugin_payload(disabled, {'plugin':'QC_ANALYSIS'}, tmp_path)
    with pytest.raises(ValueError, match='선택하지 않은.*QC_ANALYSIS'):
        run_meta_analysis_payload(disabled, {'analysis':'QC'}, tmp_path)
    with TestClient(app) as client:
        response = client.post('/api/datasets/reference-interval', json={'dataset':disabled,'options':{}})
        assert response.status_code == 400 and '선택하지 않은' in response.text, response.text[:500]


def test_selection_does_not_import_embedded_python(payload, tmp_path):
    from app.tametools_bridge import create_web_plugin_payload
    marker = tmp_path/'executed.txt'
    plugin = create_web_plugin_payload(payload, {'name':'MY_CHOICE',
        'source':f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\ndef run(dataset, options):\n    return dataset"}, tmp_path)
    chosen = select_plugins_payload(plugin, {'enabled':['MY_CHOICE']})
    assert not marker.exists()
    assert chosen['plugins'][0]['requiresTrust']
    with pytest.raises(PermissionError):
        run_web_plugin_payload(chosen, {'plugin':'MY_CHOICE'}, tmp_path)
    assert not marker.exists()
    executed = run_web_plugin_payload(chosen, {'plugin':'MY_CHOICE','allowPlugins':True}, tmp_path)
    assert marker.exists() and executed['rowCount'] == 24
    assert executed['pluginSelection']['enabled'] == ['MY_CHOICE']


def test_bad_selection_is_rejected_and_unknown_plugin_is_visible(payload):
    with TestClient(app) as client:
        for value in ['RI_EP28', {}, [None]]:
            response = client.post('/api/plugins/selection',json={'dataset':payload,'options':{'enabled':value}})
            assert response.status_code == 400
    unknown = select_plugins_payload(payload, {'enabled':['MISSING_PACKAGE']})
    assert unknown['pluginSelection']['missing'] == ['MISSING_PACKAGE']
    assert unknown['plugins'][0]['available'] is False


def test_selected_web_plugin_uses_current_workspace_source(payload, tmp_path):
    from app.tametools_bridge import create_web_plugin_payload
    items = []
    for label in ['a', 'b']:
        folder = tmp_path/label
        source = f"from tametools.models import OperationOutput\ndef run(dataset, options):\n    return OperationOutput('CUSTOM', dataset=dataset, message='{label}')"
        configured = create_web_plugin_payload(payload, {'name':'WORKSPACE_CHOICE','source':source}, folder)
        items.append((select_plugins_payload(configured, {'enabled':['WORKSPACE_CHOICE']}), folder))
    for index in [0, 1, 0]:
        configured, folder = items[index]
        result = run_web_plugin_payload(configured, {'plugin':'WORKSPACE_CHOICE','allowPlugins':True}, folder)
        ds = dataset_from_payload(result, standardize=False)
        assert log_entries(ds)[-1]['SUMMARY'] == ['a','b'][index]
