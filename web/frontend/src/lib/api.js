const API_BASE = import.meta.env.VITE_TAMETOOLS_API_BASE || '';

function requestDataset(dataset) {
  const {rows, charts, provenance, analysisTables, analysisTableFiles, issues, warnings, plugins, presets, tagCatalog, ...input} = dataset;
  return Array.isArray(dataset.dataRows) ? input : {...input, rows};
}

export async function previewSexNormalization(dataset, options) {
  return parseResponse(await fetch(`${API_BASE}/api/datasets/sex-normalization-preview`, {
    method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({dataset:requestDataset(dataset), options})
  }));
}
export async function getAnalysisTablePage(url, offset) {
  return parseResponse(await fetch(`${API_BASE}${url}?offset=${offset}&limit=50`));
}

async function parseResponse(response) {
  if (!response.ok) {
    let message = response.statusText;
    let auditFile = null;
    try {
      const body = await response.json();
      auditFile = body.auditFile || null;
      const detail = body.detail || body.error;
      message = typeof detail === 'string' ? detail : Array.isArray(detail)
        ? detail.map((item) => item.msg || '입력값을 확인해 주세요.').join(' / ') : message;
    } catch {
      message = await response.text();
    }
    const error = new Error(message);
    error.auditFile = auditFile;
    throw error;
  }
  return response.json();
}

async function nhanesRequest(path, options = {}) {
  try {
    return await parseResponse(await fetch(`${API_BASE}/api/nhanes${path}`, options));
  } catch (error) {
    if (error instanceof TypeError) throw new Error('웹앱 서버에 연결하지 못했습니다. tametools 웹 실행창이 열려 있는지 확인해 주세요.');
    throw error;
  }
}

export const getNhanesCatalog = () => nhanesRequest('/catalog');
export const getNhanesJob = (id) => nhanesRequest(`/jobs/${encodeURIComponent(id)}`);
export const cancelNhanesJob = (id) => nhanesRequest(`/jobs/${encodeURIComponent(id)}`, { method: 'DELETE' });
export const getNhanesDataset = (id) => nhanesRequest(`/jobs/${encodeURIComponent(id)}/dataset`);
export const startNhanesDownload = (cycle, fresh = false) => nhanesRequest('/downloads', {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ cycle, fresh })
});
export const startNhanesAnalysis = (downloadId, settings) => nhanesRequest('/analyses', {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ download_id: downloadId, ...settings })
});
export async function downloadNhanesFile(id, kind) {
  const response = await fetch(`${API_BASE}/api/nhanes/jobs/${encodeURIComponent(id)}/files/${encodeURIComponent(kind)}`);
  if (!response.ok) await parseResponse(response);
  return response.blob();
}

export async function openFile(file) {
  const form = new FormData();
  form.append('file', file);
  const response = await fetch(`${API_BASE}/api/files/open`, {
    method: 'POST',
    body: form
  });
  return parseResponse(response);
}

export async function convertWorkbook(workbookId, mode, sheets) {
  const response = await fetch(`${API_BASE}/api/xlsx/convert`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ workbookId, mode, sheets })
  });
  return parseResponse(response);
}

export async function runEda(dataset) {
  const response = await fetch(`${API_BASE}/api/datasets/eda`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(requestDataset(dataset))
  });
  return parseResponse(response);
}

export async function runReferenceInterval(dataset, options = {}) {
  const response = await fetch(`${API_BASE}/api/datasets/reference-interval`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ dataset:requestDataset(dataset), options })
  });
  return parseResponse(response);
}

export async function runPlannedAnalysis(dataset) {
  return parseResponse(await fetch(`${API_BASE}/api/datasets/planned-analysis`, {
    method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({dataset:requestDataset(dataset), options:{}})
  }));
}

export async function openExample(name) {
  return parseResponse(await fetch(`${API_BASE}/api/examples/${encodeURIComponent(name)}`));
}

export async function downloadAnalysisFile(file) {
  const response = await fetch(`${API_BASE}${file.url}`);
  if (!response.ok) await parseResponse(response);
  saveBlob(await response.blob(), file.filename);
}

export async function runMetaAnalysis(dataset, options = {}) {
  const response = await fetch(`${API_BASE}/api/datasets/run-analysis`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ dataset:requestDataset(dataset), options })
  });
  return parseResponse(response);
}

export async function reviewDataset(dataset) {
  const response = await fetch(`${API_BASE}/api/datasets/validate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(requestDataset(dataset))
  });
  return parseResponse(response);
}

export async function refreshDataset(dataset) {
  const response = await fetch(`${API_BASE}/api/datasets/refresh`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(requestDataset(dataset))
  });
  return parseResponse(response);
}

export async function applyDatasetFix(dataset, options) {
  const response = await fetch(`${API_BASE}/api/datasets/apply-fix`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ dataset:requestDataset(dataset), options })
  });
  return parseResponse(response);
}

export async function applyDatasetAction(dataset, options) {
  const response = await fetch(`${API_BASE}/api/datasets/apply-action`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ dataset:requestDataset(dataset), options })
  });
  return parseResponse(response);
}

export async function applyDatasetActionPipeline(dataset, options) {
  const response = await fetch(`${API_BASE}/api/datasets/apply-action-pipeline`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ dataset:requestDataset(dataset), options })
  });
  return parseResponse(response);
}

export async function applyDatasetPreset(dataset, options) {
  const response = await fetch(`${API_BASE}/api/datasets/apply-preset`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ dataset:requestDataset(dataset), options })
  });
  return parseResponse(response);
}

export async function applyDatasetTagPlacement(dataset, options) {
  const response = await fetch(`${API_BASE}/api/datasets/tag-placement`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ dataset:requestDataset(dataset), options })
  });
  return parseResponse(response);
}

export async function createDatasetPlugin(dataset, options) {
  const response = await fetch(`${API_BASE}/api/plugins/create`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ dataset:requestDataset(dataset), options })
  });
  return parseResponse(response);
}

export async function runDatasetPlugin(dataset, options) {
  const response = await fetch(`${API_BASE}/api/plugins/run`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ dataset:requestDataset(dataset), options })
  });
  return parseResponse(response);
}

export async function anonymizeDataset(dataset, options) {
  const response = await fetch(`${API_BASE}/api/datasets/anonymize`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ dataset:requestDataset(dataset), options })
  });
  return parseResponse(response);
}

export async function downloadDataset(dataset, format) {
  const endpoint = format === 'xlsx' ? 'xlsx' : 'tame';
  const response = await fetch(`${API_BASE}/api/datasets/${endpoint}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(requestDataset(dataset))
  });
  if (!response.ok) {
    const body = await response.text();
    throw new Error(body || response.statusText);
  }
  return response.blob();
}

export function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

export async function inspectProvenance(dataset) {
  return parseResponse(await fetch(`${API_BASE}/api/datasets/provenance`, {
    method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(requestDataset(dataset))
  }));
}
export async function exportProvenance(dataset) {
  return parseResponse(await fetch(`${API_BASE}/api/datasets/provenance/export`, {
    method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(requestDataset(dataset))
  }));
}
