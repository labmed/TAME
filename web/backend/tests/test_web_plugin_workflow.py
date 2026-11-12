"""The same declared QC workflow through the common web workbench."""
from pathlib import Path
import io
import zipfile
import json
import pandas as pd
from fastapi.testclient import TestClient
from tametools.io import read_tame
from app.main import app
from app.tametools_bridge import dataset_payload, run_web_plugin_payload

ROOT = Path(__file__).resolve().parents[3]

def test_web_plugin_respects_meta_settings_and_explicit_override(tmp_path):
    ds = read_tame(ROOT/'web/backend/examples/qc_workflow.tame')
    payload = dataset_payload(ds)
    result = run_web_plugin_payload(payload, {'plugin':'QC_ANALYSIS', 'pluginOptions':{}}, tmp_path)
    assert result['rowCount'] == 48
    assert len(result['charts']) == 2
    assert any(row['y'] is None for chart in result['charts'] for row in chart['rows'])
    assert result['charts'][0]['referenceLines']
    precision = run_web_plugin_payload(payload, {'plugin':'QC_ANALYSIS', 'pluginOptions':{'mode':'PRECISION'}}, tmp_path)
    assert precision['rowCount'] == 8


def test_qc_example_workflow_report_download_and_reload(tmp_path):
    with TestClient(app) as client:
        source = client.get('/api/examples/qc').json()
        assert source['rowCount'] == 24 and source['defaultPlugin'] == 'QC_WORKFLOW'
        assert source['issues'] == []
        request = {'dataset':source,'options':{'plugin':'QC_WORKFLOW','pluginOptions':{'REPORT_MAX_ROWS':3}}}
        response = client.post('/api/plugins/run', json=request)
        assert response.status_code == 200, response.text[:2000]
        result = response.json()
        assert result['dataRole'] == 'result' and result['resultType'] == 'workflow'
        assert len(result['workflow']['STEPS']) == 6
        assert len(result['charts']) == 5
        assert len(result['analysisTables']['rules.main']) == 48
        assert result['issues'] == []
        for file in result['reportFiles']:
            download = client.get(file['url'])
            assert download.status_code == 200
            if file['filename'] == 'report.html':
                assert 'data:image/png;base64,' in download.text
            elif file['filename'] == 'report.zip':
                with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
                    p = tmp_path/'result.tame'; p.write_bytes(archive.read('result.tame'))
                    saved = dataset_payload(read_tame(p))
                    assert len(saved['charts']) == 5
                    assert any(row['y'] is None for chart in saved['charts'] for row in chart['rows'])
                    assert len(pd.read_csv(archive.open('tables/rules.main.csv'))) == 48
        request['options']['pluginOptions']['OUTPUT_DIR'] = str(tmp_path/'unauthorized')
        forbidden = client.post('/api/plugins/run', json=request)
        assert forbidden.status_code == 400 and 'OUTPUT_DIR' in forbidden.text
        assert not (tmp_path/'unauthorized').exists()


def test_qualified_table_paging_and_csv_download():
    from app.main import store
    from app.workbench_analysis import attach_workflow_report
    from tametools.plugin_workflow import table_output
    name = 'rules.main'
    identifier = 'a'*24
    folder = store.path/'analysis_reports'/identifier
    (folder/'tables').mkdir(parents=True)
    frame = pd.DataFrame({'value':range(205)})
    frame.to_csv(folder/'tables'/f'{name}.csv', index=False)
    out = table_output('dummy', frame); out.tables={name:frame}
    response = {}
    attach_workflow_report(response,out,identifier,store.path)
    with TestClient(app) as client:
        urls = response['analysisTableFiles'][name]
        page = client.get(urls['pageUrl'],params={'offset':200,'limit':5})
        assert page.status_code == 200 and len(page.json()['rows']) == 5
        assert client.get(urls['downloadUrl']).status_code == 200
        assert client.get(f'/api/analysis-tables/{identifier}/absent.main').status_code == 404
