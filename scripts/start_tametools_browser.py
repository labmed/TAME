#!/usr/bin/env python3
"""Start the local Chrome app using an isolated Python environment and built UI."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import venv

ROOT = Path(__file__).resolve().parents[1]
APPLICATION = 'tametools-browser'


def parser():
    p = argparse.ArgumentParser(description='Run tametools in Chrome without a tametools EXE.')
    p.add_argument('--port', type=int, default=8765, help='Preferred local port (default: 8765).')
    p.add_argument('--runtime-dir', type=Path, default=ROOT/'.browser-runtime', help='Isolated environment and download cache directory.')
    p.add_argument('--no-browser', action='store_true', help='Print the URL without opening Chrome.')
    p.add_argument('--prepare-only', action='store_true', help='Install the environment, then exit.')
    p.add_argument('--repair', action='store_true', help='Recreate only this app\'s Python environment; retain downloaded data.')
    p.add_argument('--_serve', action='store_true', help=argparse.SUPPRESS)
    return p


def say(message):
    print(f'[tametools] {message}', flush=True)


def check_source(root=ROOT):
    needed=['web/frontend/build/index.html','web/backend/app/main.py',
            'tametools/pyproject.toml','packaging/browser/requirements.txt']
    missing=[name for name in needed if not (root/name).is_file()]
    if missing:
        raise RuntimeError('배포 ZIP 전체를 먼저 풀어 주세요. 필요한 파일이 없습니다: '+', '.join(missing))


def fingerprint(root=ROOT):
    h=hashlib.sha256()
    for name in ['tametools/pyproject.toml','packaging/browser/requirements.txt']:
        h.update(name.encode());h.update((root/name).read_bytes())
    for p in sorted((root/'tametools/src').rglob('*.py')):
        h.update(p.relative_to(root).as_posix().encode());h.update(p.read_bytes())
    h.update(f'{sys.version_info[:2]}:{sys.platform}:{platform.machine()}:{sys.base_prefix}'.encode())
    return h.hexdigest()


def instance_id(root=ROOT):
    h=hashlib.sha256(str(root.resolve()).encode())
    for p in sorted((root/'web/backend/app').glob('*.py')):h.update(p.read_bytes())
    h.update((root/'web/frontend/build/index.html').read_bytes())
    return h.hexdigest()[:24]


def environment_python(runtime):
    return runtime/'venv'/('Scripts/python.exe' if os.name=='nt' else 'bin/python')


def run(command):
    subprocess.run([str(part) for part in command],check=True)


def lock_setup(path):
    handle=open(path,'a+b')
    try:
        if handle.tell()==0:handle.write(b'0');handle.flush()
        handle.seek(0)
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise RuntimeError('다른 시작 창에서 설치 중입니다. 해당 창의 설치가 끝난 뒤 다시 실행하세요.')
    return handle  # The operating system releases this lock even after a crash.


def prepare_environment(runtime, repair=False):
    runtime.mkdir(parents=True,exist_ok=True)
    python=environment_python(runtime)
    state_file=runtime/'environment.json'
    expected=fingerprint()
    try:state=json.loads(state_file.read_text(encoding='utf-8'))
    except (OSError,ValueError):state={}
    if not repair and python.is_file() and state.get('fingerprint')==expected:
        say('준비된 실행 환경을 사용합니다. 패키지를 다시 설치하지 않습니다.')
        return python
    lock=lock_setup(runtime/'setup.lock')
    try:
        if repair or (python.exists() and state.get('python_base') not in {None,sys.base_prefix}):
            # This directory contains only the environment created by this launcher.
            shutil.rmtree(runtime/'venv')
        if not python.is_file():
            say('이 앱만 사용하는 Python 환경을 준비합니다.')
            venv.EnvBuilder(with_pip=True).create(runtime/'venv')
        say('첫 실행에 필요한 분석 패키지를 설치합니다. 인터넷 연결이 필요하며 수 분 걸릴 수 있습니다.')
        run([python,'-m','pip','install','--upgrade','pip','setuptools>=68','wheel'])
        run([python,'-m','pip','install','--only-binary=:all:','-r',ROOT/'packaging/browser/requirements.txt'])
        run([python,'-m','pip','install','--no-deps','--no-build-isolation',ROOT/'tametools'])
        run([python,'-m','pip','check'])
        run([python,'-c','import fastapi, uvicorn, pyreadstat, samplics, scipy, tametools; assert tametools.__version__ == "0.4.0"'])
        state_file.write_text(json.dumps(dict(fingerprint=expected,python_base=sys.base_prefix,
            python_version=platform.python_version(),platform=platform.platform()),indent=2),encoding='utf-8')
        say('분석 환경 설치를 완료했습니다.')
        return python
    finally:
        lock.close()


def read_status(port):
    # Bypass proxy environment settings for a loopback-only health request.
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f'http://127.0.0.1:{port}/api/browser/status',timeout=.6) as response:
            return json.load(response)
    except (OSError,ValueError):return None


def choose_port(preferred, identity):
    if not 1024<=preferred<=65515:raise ValueError('포트는 1024–65515 사이로 지정해 주세요.')
    for port in range(preferred,preferred+20):
        status=read_status(port)
        if status and status.get('application')==APPLICATION and status.get('instance')==identity:
            return port,True
        with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as sock:
            try:sock.bind(('127.0.0.1',port))
            except OSError:continue
        return port,False
    raise RuntimeError('사용 가능한 포트를 찾지 못했습니다. --port 8865처럼 다른 포트를 지정해 주세요.')


def find_chrome():
    configured=os.environ.get('TAMETOOLS_CHROME')
    if configured:
        path=Path(configured)
        if not path.is_file():raise RuntimeError('TAMETOOLS_CHROME에 지정한 Chrome 실행 경로를 찾지 못했습니다.')
        return str(path)
    if os.name=='nt':
        for base in ['PROGRAMFILES','PROGRAMFILES(X86)','LOCALAPPDATA']:
            if os.environ.get(base):
                path=Path(os.environ[base])/'Google/Chrome/Application/chrome.exe'
                if path.is_file():return str(path)
    if sys.platform=='darwin':
        for path in [Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'),
                     Path.home()/'Applications/Google Chrome.app/Contents/MacOS/Google Chrome']:
            if path.is_file():return str(path)
    for name in ['google-chrome','google-chrome-stable','chromium','chromium-browser']:
        path=shutil.which(name)
        if path:return path
    return None


def open_chrome(url):
    try:
        chrome=find_chrome()
        if chrome:
            subprocess.Popen([chrome,'--new-window',url],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        else:say('Chrome 위치를 찾지 못했습니다. Chrome 주소창에 다음 주소를 붙여 넣으세요: '+url)
    except (OSError,RuntimeError) as exc:say(f'Chrome을 자동으로 열지 못했습니다: {exc}. 접속 주소: {url}')


def serve(args, identity):
    runtime=args.runtime_dir.resolve()
    os.environ['TAMETOOLS_FRONTEND_BUILD_DIR']=str(ROOT/'web/frontend/build')
    os.environ['TAMETOOLS_NHANES_CACHE']=str(runtime/'nhanes')
    os.environ['TAMETOOLS_BROWSER_INSTANCE']=identity
    os.environ['MPLBACKEND']='Agg'
    sys.path[:0]=[str(ROOT/'tametools/src'),str(ROOT/'web/backend')]
    port,reuse=choose_port(args.port,identity)
    url=f'http://127.0.0.1:{port}'
    if reuse:
        say('이미 실행 중인 이 버전의 웹앱에 연결합니다: '+url)
        if not args.no_browser:open_chrome(url)
        return 0
    say('Chrome 접속 주소: '+url)
    say('이 컴퓨터 안에서만 실행합니다. 종료하려면 이 창에서 Ctrl+C를 누르세요.')
    if not args.no_browser:
        def when_ready():
            for _ in range(120):
                status=read_status(port)
                if status and status.get('instance')==identity:
                    open_chrome(url);return
                time.sleep(.5)
            say('자동 접속 대기 시간이 지났습니다. 위 주소를 Chrome에서 직접 열어 주세요.')
        threading.Thread(target=when_ready,daemon=True).start()
    import uvicorn
    uvicorn.run('app.main:app',host='127.0.0.1',port=port,workers=1,log_level='info')
    return 0


def main(argv=None):
    args=parser().parse_args(argv)
    try:
        if not (3,10)<=sys.version_info[:2]<(3,14):
            raise RuntimeError('이 배포본은 64비트 Python 3.10–3.13을 지원합니다. 해당 버전의 Python으로 실행해 주세요.')
        if sys.maxsize<=2**32:raise RuntimeError('64비트 Python이 필요합니다.')
        check_source()
        identity=instance_id()
        if args._serve:return serve(args,identity)
        port,reuse=(args.port,False) if args.prepare_only else choose_port(args.port,identity)
        if reuse and args.repair:
            raise RuntimeError('실행 중인 앱의 시작 창을 Ctrl+C로 종료한 뒤 --repair를 실행해 주세요.')
        if reuse and not args.prepare_only and not args.repair:
            url=f'http://127.0.0.1:{port}';say('이미 실행 중인 웹앱에 연결합니다: '+url)
            if not args.no_browser:open_chrome(url)
            return 0
        python=prepare_environment(args.runtime_dir.resolve(),args.repair)
        if args.prepare_only:return 0
        command=[str(python),'-u',str(Path(__file__).resolve()),'--_serve','--port',str(port),
                 '--runtime-dir',str(args.runtime_dir.resolve())]
        if args.no_browser:command.append('--no-browser')
        return subprocess.call(command)
    except KeyboardInterrupt:
        say('실행을 종료했습니다.');return 0
    except (OSError,RuntimeError,ValueError,subprocess.CalledProcessError) as exc:
        say('시작하지 못했습니다: '+str(exc))
        say('설치 중 네트워크가 끊겼다면 연결을 확인하고 다시 시작하세요. 환경 복구: --repair')
        return 1


if __name__=='__main__':
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8',errors='replace')
    raise SystemExit(main())
