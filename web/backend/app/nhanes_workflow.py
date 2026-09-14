"""Local background jobs for downloading, reviewing and analysing public NHANES data."""
from concurrent.futures import ThreadPoolExecutor, CancelledError
from copy import deepcopy
import json
import logging
import os
from pathlib import Path
import secrets
import threading
import zipfile
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from tametools.io import write_tame
from tametools.nhanes import download_nhanes
from .nhanes_data import catalog, file_ids, recipe, prepare_dataset, make_plan, run_analysis
from .nhanes_report import result_payload, write_report
from .tametools_bridge import dataset_payload
from .workbench_analysis import dependency_message, require_example_dependencies

log=logging.getLogger(__name__)


class DownloadSettings(BaseModel):
    model_config={'extra':'forbid'}
    cycle: Literal['2017-2018','2021-2023']='2021-2023'
    fresh: bool=False


class AnalysisSettings(BaseModel):
    model_config={'extra':'forbid'}
    download_id: str
    variables: list[str]=Field(default_factory=lambda:['ALT','CREATININE','ALBUMIN'])
    age_group: str='adults'
    by_sex: bool=True
    policy: Literal['RELEASED','DELETE']='RELEASED'


def default_cache():
    if os.environ.get('TAMETOOLS_NHANES_CACHE'):
        return Path(os.environ['TAMETOOLS_NHANES_CACHE'])
    base=Path(os.environ.get('LOCALAPPDATA') or os.environ.get('XDG_CACHE_HOME') or Path.home()/'.cache')
    return base/'tametools'/'nhanes'


class NhanesService:
    def __init__(self, workspace, cache=None, downloader=download_nhanes):
        self.workspace=Path(workspace)/'nhanes'
        self.workspace.mkdir(parents=True,exist_ok=True)
        self.cache=Path(cache) if cache is not None else default_cache()
        self.downloader=downloader
        self.jobs={}
        self.lock=threading.RLock()
        self.pool=ThreadPoolExecutor(max_workers=2,thread_name_prefix='nhanes')

    def _new_job(self, kind, total, **extra):
        with self.lock:
            if len(self.jobs)>=64:
                raise HTTPException(429,'작업이 많이 쌓였습니다. 필요한 결과를 저장한 뒤 앱을 다시 열어 주세요.')
            job_id=secrets.token_urlsafe(18)
            job=dict(id=job_id,kind=kind,status='queued',completed=0,total=total,
                     message='작업을 준비하고 있습니다.',events=[],error='',cancel=threading.Event(),**extra)
            self.jobs[job_id]=job
            return job

    def _job(self, job_id):
        with self.lock:
            if job_id not in self.jobs:
                raise HTTPException(404,'이전 작업을 찾을 수 없습니다. 앱이 재시작된 경우 자료를 다시 열어 주세요. 검증된 다운로드 파일은 재사용합니다.')
            return self.jobs[job_id]

    def status(self,job_id):
        with self.lock:
            job=self._job(job_id)
            fields={k:job[k] for k in ['id','kind','status','completed','total','message','error']}
            fields['events']=list(job['events'])
            if job['status']=='ready':fields['result']=deepcopy(job['result'])
            return fields

    def _progress(self,job,message,completed):
        if job['cancel'].is_set():raise CancelledError()
        with self.lock:
            job.update(status='running',message=message,completed=completed)
            job['events'].append(message)

    def _finish(self,job,result):
        if job['cancel'].is_set():raise CancelledError()
        with self.lock:
            job.update(status='ready',message='완료했습니다.',completed=job['total'],result=result)

    def _failed(self,job,exc):
        with self.lock:
            if isinstance(exc,CancelledError):
                job.update(status='cancelled',message='작업을 취소했습니다. 받은 파일은 다음 시도에서 확인 후 재사용합니다.')
            else:
                log.exception('NHANES %s job failed',job['kind'])
                if isinstance(exc,ImportError):
                    message=dependency_message(exc)
                elif 'download failed' in str(exc).lower():
                    message='CDC 서버에서 자료를 받지 못했습니다. 인터넷 연결을 확인한 뒤 다시 시도해 주세요.'
                elif any(word in str(exc).lower() for word in ['checksum','untracked','locked','snapshot','manifest']):
                    message='저장된 자료를 검증하지 못했습니다. 잠시 후 다시 시도하거나 새로 받기를 선택해 주세요.'
                elif isinstance(exc,ValueError):message=str(exc)
                else:message='작업을 완료하지 못했습니다. 설정을 확인하고 다시 시도해 주세요.'
                job.update(status='error',error=message,message=message)

    def start_download(self,settings):
        cycle=settings.cycle
        recipe(cycle)
        with self.lock:
            for job in self.jobs.values():
                if job['kind']=='download' and job['cycle']==cycle and job['status'] in {'queued','running','ready'}:
                    if job['status']!='ready' or not settings.fresh:return dict(job_id=job['id'])
            job=self._new_job('download',5,cycle=cycle)
            job['cache']=self.cache/(cycle+'-'+job['id'] if settings.fresh else cycle)
            self.pool.submit(self._download,job)
        return dict(job_id=job['id'])

    def _download(self,job):
        try:
            require_example_dependencies()
            self._progress(job,'CDC 공식 자료와 설명서를 확인합니다.',0)
            def progress(message):
                with self.lock: completed=job['completed']+1
                file=message.split(':',1)[-1].split('(')[0].strip()
                verb='저장된 파일 검증' if message.startswith('verified_cache') else '다운로드 완료'
                self._progress(job,f'{verb}: {file}',completed)
            self.downloader(job['cache'],cycle=job['cycle'],files=file_ids(job['cycle']),progress=progress,timeout=30)
            self._progress(job,'참여자 ID로 연결하고 단위·가중치·검사값을 검증합니다.',4)
            ds,info=prepare_dataset(job['cache'],job['cycle'])
            folder=self.workspace/job['id'];folder.mkdir()
            write_tame(folder/'data.tame',ds)
            archive=folder/'nhanes_sources.zip'
            with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
                for p in sorted((job['cache']/'sources').iterdir()):
                    if p.name in {f'{name}.{ext}' for name in file_ids(job['cycle']) for ext in ['xpt','htm']}:
                        z.write(p,'sources/'+p.name)
                z.write(job['cache']/'source_manifest.json','source_manifest.json')
            with self.lock:job.update(dataset=ds,files=dict(tame=folder/'data.tame',sources=archive))
            self._finish(job,info)
        except Exception as exc:self._failed(job,exc)

    def start_analysis(self,settings):
        source=self._job(settings.download_id)
        if source['kind']!='download' or source['status']!='ready':
            raise HTTPException(409,'자료 준비가 완료된 뒤 분석해 주세요.')
        options=settings.model_dump(exclude={'download_id'})
        if options['policy']=='DELETE' and not source['result']['censoring_available']:
            raise HTTPException(400,'이 시기의 선택 검사에는 검출한계 표시가 없어 공개값 유지 정책을 사용해야 합니다.')
        try:make_plan(options)
        except ValueError as exc:raise HTTPException(400,str(exc)) from exc
        job=self._new_job('analysis',4,download_id=source['id'])
        self.pool.submit(self._analyze,job,source,options)
        return dict(job_id=job['id'])

    def _analyze(self,job,source,options):
        try:
            self._progress(job,'선택한 대상과 검사 항목을 적용합니다.',1)
            output=run_analysis(source['dataset'],options)
            self._progress(job,'가중 평균·신뢰구간과 관측값 분포를 정리합니다.',2)
            result=result_payload(output,options,source['result'])
            self._progress(job,'TAME·Excel·한글 보고서를 저장합니다.',3)
            folder=self.workspace/job['id'];folder.mkdir()
            files=write_report(output,result,source['result'],folder/'report',source['cache'])
            with self.lock:job.update(files=files)
            self._finish(job,result)
        except Exception as exc:self._failed(job,exc)

    def cancel(self,job_id):
        with self.lock:
            job=self._job(job_id)
            if job['status'] in {'queued','running','cancelling'}:
                job['cancel'].set()
                job.update(status='cancelling',message='현재 파일 요청이 끝나면 취소합니다.')
            return self.status(job_id)

    def artifact(self,job_id,kind):
        job=self._job(job_id)
        if job['status']!='ready':raise HTTPException(409,'파일이 아직 준비되지 않았습니다.')
        if kind not in job.get('files',{}):raise HTTPException(404,'요청한 저장 형식이 없습니다.')
        path=job['files'][kind]
        return FileResponse(path,filename=path.name)

    def advanced_dataset(self,job_id):
        job=self._job(job_id)
        if job['status']!='ready':raise HTTPException(409,'자료 준비를 기다려 주세요.')
        if job['kind']=='download':dataset=job['dataset']
        else:
            from tametools.io import read_tame
            dataset=read_tame(job['files']['tame'])
        return dataset_payload(dataset,filename=f'NHANES_{job.get("cycle") or job["result"]["cycle"]}.tame')

    def close(self):
        with self.lock:
            for job in self.jobs.values():
                if job['status'] in {'queued','running','cancelling'}:job['cancel'].set()
        self.pool.shutdown(wait=True,cancel_futures=False)


def create_router(workspace):
    service=NhanesService(workspace)
    router=APIRouter(prefix='/api/nhanes',tags=['Public example data'])

    @router.get('/catalog')
    def get_catalog():return catalog()

    @router.post('/downloads',status_code=202)
    def download(settings:DownloadSettings):return service.start_download(settings)

    @router.get('/jobs/{job_id}')
    def status(job_id:str):return service.status(job_id)

    @router.delete('/jobs/{job_id}')
    def cancel(job_id:str):return service.cancel(job_id)

    @router.post('/analyses',status_code=202)
    def analyze(settings:AnalysisSettings):return service.start_analysis(settings)

    @router.get('/jobs/{job_id}/files/{kind}')
    def artifact(job_id:str,kind:str):return service.artifact(job_id,kind)

    @router.get('/jobs/{job_id}/dataset')
    def dataset(job_id:str):return service.advanced_dataset(job_id)

    @router.on_event('shutdown')
    def shutdown():service.close()
    return router
