from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import secrets
import tempfile
from typing import Any

from tametools import __version__

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, FileResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from .provenance_ui import recorded_dataset
from tametools.provenance import append_log_entry, log_entries, write_failure_record
from tametools.provenance_audit import history_payload
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .tametools_bridge import (
    StoredUpload,
    anonymize_payload,
    apply_action_pipeline_payload,
    apply_meta_action_payload,
    apply_preset_payload,
    apply_tag_placement_payload,
    apply_validation_fix_payload,
    convert_xlsx_selection,
    create_web_plugin_payload,
    dataset_from_payload,
    dataset_payload,
    dataset_to_tame_bytes,
    dataset_to_xlsx_bytes,
    eda_payload,
    inspect_xlsx,
    is_tame_workbook,
    load_tame_payload,
    load_tame_workbook_payload,
    reference_interval_payload,
    refresh_payload,
    run_meta_analysis_payload,
    run_web_plugin_payload,
    validation_review_payload,
)


FRONTEND_BUILD_DIR = Path(
    os.environ.get("TAMETOOLS_FRONTEND_BUILD_DIR") or Path(__file__).resolve().parents[2] / "frontend" / "build"
)


@dataclass
class UploadStore:
    workspace: tempfile.TemporaryDirectory
    uploads: dict[str, StoredUpload]

    @classmethod
    def create(cls) -> "UploadStore":
        return cls(workspace=tempfile.TemporaryDirectory(prefix="tametools-fastapi-"), uploads={})

    @property
    def path(self) -> Path:
        return Path(self.workspace.name)

    def save(self, filename: str, content: bytes) -> StoredUpload:
        upload_id = secrets.token_urlsafe(12)
        suffix = Path(filename).suffix.lower()
        path = self.path / f"{upload_id}{suffix}"
        path.write_bytes(content)
        upload = StoredUpload(upload_id=upload_id, filename=filename, path=path)
        self.uploads[upload_id] = upload
        return upload


class XlsxConvertRequest(BaseModel):
    workbook_id: str = Field(alias="workbookId")
    mode: str = "single"
    sheets: list[str] = Field(default_factory=list)


class AnonymizeRequest(BaseModel):
    dataset: dict[str, Any]
    options: dict[str, Any] = Field(default_factory=dict)


class DatasetFixRequest(BaseModel):
    dataset: dict[str, Any]
    options: dict[str, Any] = Field(default_factory=dict)


class AnalysisRequest(BaseModel):
    dataset: dict[str, Any]
    options: dict[str, Any] = Field(default_factory=dict)


class PluginRequest(BaseModel):
    dataset: dict[str, Any]
    options: dict[str, Any] = Field(default_factory=dict)


app = FastAPI(title="tametools web backend", version=__version__)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_origin_regex=r"^http://(127\.0\.0\.1|localhost):51[0-9]{2}$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
store = UploadStore.create()
from .nhanes_workflow import create_router
app.include_router(create_router(store.path))


@app.exception_handler(StarletteHTTPException)
async def execution_error_handler(request, exc):
    detail = {'detail': exc.detail}
    cause = exc
    while cause is not None and not hasattr(cause, 'audit_dataset'):
        cause = cause.__cause__
    if cause is not None:
        try:
            path = write_failure_record(cause, store.path / 'execution_records')
            if path:
                detail['auditFile'] = dict(label='실패 실행 기록 저장', url='/api/execution-records/' + path.name, filename=path.name)
        except Exception as record_error:
            detail['auditError'] = '실패 기록을 저장하지 못했습니다: ' + str(record_error)
    return JSONResponse(status_code=exc.status_code, content=detail, headers=exc.headers)


@app.get('/api/execution-records/{name}')
def execution_record_file(name: str):
    import re
    if not re.fullmatch(r'failed-[0-9a-f-]+\.json', name):
        raise HTTPException(404, 'Unknown execution record')
    path = store.path / 'execution_records' / name
    if not path.is_file():
        raise HTTPException(404, 'Unknown execution record')
    return FileResponse(path, media_type='application/json', filename=name)


@app.post('/api/datasets/provenance')
def inspect_provenance(payload: dict[str, Any]):
    try:
        import re
        from tametools.config import ci_get
        dataset = dataset_from_payload(payload, standardize=False)
        report_id = ci_get(ci_get(dataset.meta, 'WEB_WORKBENCH', {}), 'REPORT_ID', '')
        folder = store.path / 'analysis_reports' / report_id if re.fullmatch(r'[A-Za-z0-9_-]+', str(report_id)) else None
        return history_payload(dataset, artifacts_dir=folder if folder and folder.is_dir() else None)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post('/api/datasets/provenance/export')
def export_provenance(payload: dict[str, Any]):
    try:
        from tametools.config import ci_get
        dataset = dataset_from_payload(payload, standardize=False)
        return {'VERSION': 2, 'LOG': log_entries(dataset), 'PROVENANCE': ci_get(dataset.meta, 'PROVENANCE', {}),
                'verification': history_payload(dataset)['verification']}
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/browser/status")
def browser_status() -> dict[str, str]:
    return {"application": "tametools-browser", "version": __version__,
            "instance": os.environ.get("TAMETOOLS_BROWSER_INSTANCE", ""),
            "processing": "local"}


@app.post("/api/files/open")
async def open_file(file: UploadFile = File(...)) -> dict[str, Any]:
    filename = Path(file.filename or "upload").name
    content = await file.read()
    upload = store.save(filename, content)
    suffix = upload.path.suffix.lower()

    try:
        if suffix in {".xlsx", ".xlsm"}:
            if is_tame_workbook(upload.path):
                return load_tame_workbook_payload(upload.path, filename=filename)
            return {
                "kind": "xlsx",
                "workbookId": upload.upload_id,
                "filename": filename,
                "sheets": inspect_xlsx(upload.path),
            }
        if suffix == ".tame" or filename.lower().endswith((".data.tame", ".meta.tame")):
            return load_tame_payload(upload.path, filename=filename)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    raise HTTPException(status_code=400, detail=f"Unsupported file type: {filename}")


@app.post("/api/xlsx/convert")
def convert_xlsx(request: XlsxConvertRequest) -> dict[str, Any]:
    upload = store.uploads.get(request.workbook_id)
    if upload is None:
        raise HTTPException(status_code=404, detail="Unknown workbook. Upload the Excel file again.")
    try:
        dataset, warnings = convert_xlsx_selection(upload.path, sheet_names=request.sheets, mode=request.mode)
        return dataset_payload(dataset, filename=upload.filename, warnings=warnings)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/datasets/tame")
def download_tame(payload: dict[str, Any]) -> Response:
    try:
        dataset = recorded_dataset(payload)
        dataset = append_log_entry(dataset, action='EXPORT_TAME', input_dataset=dataset,
            output=str(payload.get('filename','dataset')), parameters={'tag_storage':payload.get('tagStorage','preserve')},
            message='검토용 데이터와 분석 이력을 함께 저장했습니다.')
        content = dataset_to_tame_bytes(dataset, store.path, tag_storage=str(payload.get("tagStorage") or "preserve"))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(
        content=content,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="dataset.tame"'},
    )


@app.post("/api/datasets/xlsx")
def download_xlsx(payload: dict[str, Any]) -> Response:
    try:
        dataset = recorded_dataset(payload)
        dataset = append_log_entry(dataset, action='EXPORT_XLSX', input_dataset=dataset,
            output=str(payload.get('filename','dataset')), parameters={'tag_storage':payload.get('tagStorage','preserve')},
            message='검토용 데이터와 분석 이력을 함께 저장했습니다.')
        content = dataset_to_xlsx_bytes(dataset, store.path, tag_storage=str(payload.get("tagStorage") or "preserve"))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="dataset.xlsx"'},
    )


@app.post("/api/datasets/eda")
def run_eda(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return eda_payload(payload)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/datasets/reference-interval")
def run_reference_interval(request: AnalysisRequest) -> dict[str, Any]:
    try:
        from .workbench_analysis import reference_analysis
        return reference_analysis(request.dataset, request.options, store.path)
    except Exception as exc:
        from .workbench_analysis import dependency_message
        raise HTTPException(status_code=400, detail=dependency_message(exc) if isinstance(exc, ImportError) else str(exc)) from exc


@app.post('/api/datasets/planned-analysis')
def run_planned_analysis(request: AnalysisRequest):
    try:
        from .workbench_analysis import planned_analysis
        return planned_analysis(request.dataset)
    except Exception as exc:
        from .workbench_analysis import dependency_message
        raise HTTPException(400, dependency_message(exc) if isinstance(exc,ImportError) else str(exc)) from exc


@app.post('/api/datasets/sex-normalization-preview')
def preview_sex_normalization(request: DatasetFixRequest):
    try:
        from tametools.sex_normalization import sex_normalization_preview
        return sex_normalization_preview(dataset_from_payload(request.dataset, standardize=False), request.options)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


def _analysis_table(report_id, name):
    import re, json
    if not re.fullmatch(r'[A-Za-z0-9_-]{24}', report_id) or not re.fullmatch(r'[a-z_]+', name):
        raise HTTPException(404, '감사표를 찾을 수 없습니다.')
    folder = store.path/'analysis_reports'/report_id
    index_file = folder/'table_index.json'
    if not index_file.is_file():
        raise HTTPException(404, '감사표를 찾을 수 없습니다. 웹앱을 재시작했다면 분석을 다시 실행해 주세요.')
    index = json.loads(index_file.read_text(encoding='utf-8'))
    if name not in index or not (folder/'tables'/f'{name}.csv').is_file():
        raise HTTPException(404, '감사표를 찾을 수 없습니다.')
    return folder/'tables'/f'{name}.csv', index[name]


@app.get('/api/analysis-tables/{report_id}/{name}')
def analysis_table_page(report_id: str, name: str, offset: int = 0, limit: int = 50):
    import csv, itertools
    if offset < 0 or not 1 <= limit <= 200:
        raise HTTPException(400, '페이지 크기는 1–200행, 시작 위치는 0 이상이어야 합니다.')
    path, info = _analysis_table(report_id, name)
    with path.open(encoding='utf-8-sig', newline='') as stream:
        rows = list(itertools.islice(csv.DictReader(stream), offset, offset+limit))
    return dict(**info, rows=rows, offset=offset)


@app.get('/api/analysis-tables/{report_id}/{name}/csv')
def analysis_table_csv(report_id: str, name: str):
    path, _info = _analysis_table(report_id, name)
    return FileResponse(path, filename=name+'.csv', media_type='text/csv; charset=utf-8')


@app.get('/api/analysis-reports/{report_id}/{filename}')
def analysis_report(report_id: str, filename: str):
    import re
    if not re.fullmatch(r'[A-Za-z0-9_-]{24}',report_id) or filename not in {'reference_interval_report_ko.docx','reference_interval_report.zip'}:
        raise HTTPException(404,'보고서를 찾을 수 없습니다.')
    path=store.path/'analysis_reports'/report_id/filename
    if not path.is_file():raise HTTPException(404,'보고서를 찾을 수 없습니다. 웹앱 재시작 후에는 분석을 다시 실행해 주세요.')
    return FileResponse(path,filename=filename)


@app.get('/api/examples/{name}')
def example_dataset(name: str):
    if name != 'kenya':raise HTTPException(404,'예제 자료를 찾을 수 없습니다.')
    path=Path(__file__).resolve().parents[1]/'examples/kenya_reference.tame'
    if not path.is_file():raise HTTPException(404,'예제 파일이 누락되었습니다. examples가 포함된 배포본을 확인해 주세요.')
    return load_tame_payload(path,filename='Kenya_reference_533.tame')


@app.post("/api/datasets/validate")
def validate(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return validation_review_payload(payload)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/datasets/refresh")
def refresh(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return refresh_payload(payload)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/datasets/apply-fix")
def apply_fix(request: DatasetFixRequest) -> dict[str, Any]:
    try:
        return apply_validation_fix_payload(request.dataset, request.options)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/datasets/apply-action")
def apply_action(request: DatasetFixRequest) -> dict[str, Any]:
    try:
        return apply_meta_action_payload(request.dataset, request.options)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/datasets/apply-action-pipeline")
def apply_action_pipeline(request: DatasetFixRequest) -> dict[str, Any]:
    try:
        return apply_action_pipeline_payload(request.dataset, request.options)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/datasets/run-analysis")
def run_meta_analysis(request: AnalysisRequest) -> dict[str, Any]:
    try:
        return run_meta_analysis_payload(request.dataset, request.options, store.path)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/datasets/apply-preset")
def apply_preset_route(request: DatasetFixRequest) -> dict[str, Any]:
    try:
        return apply_preset_payload(request.dataset, request.options)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/plugins/create")
def create_plugin(request: PluginRequest) -> dict[str, Any]:
    try:
        return create_web_plugin_payload(request.dataset, request.options, store.path)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/plugins/run")
def run_plugin(request: PluginRequest) -> dict[str, Any]:
    try:
        return run_web_plugin_payload(request.dataset, request.options, store.path)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/datasets/tag-placement")
def apply_tag_placement(request: DatasetFixRequest) -> dict[str, Any]:
    try:
        return apply_tag_placement_payload(request.dataset, request.options)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/datasets/anonymize")
def anonymize(request: AnonymizeRequest) -> dict[str, Any]:
    try:
        return anonymize_payload(request.dataset, request.options)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


if FRONTEND_BUILD_DIR.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_BUILD_DIR, html=True), name="frontend")
