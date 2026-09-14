"""Verified, resumable downloads of explicitly selected NHANES public-use files."""
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
import math
import os
from pathlib import Path
import re
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

CYCLES = {f"{year}-{year+1}": year for year in range(1999, 2018, 2)}
CYCLES["2021-2023"] = 2021
DATA_USE_URL = "https://www.cdc.gov/nchs/policy/data-user-agreement.html"
MAX_BYTES = 128 * 1024 * 1024


def _base(cycle):
    if cycle not in CYCLES:
        raise ValueError("Unsupported release cycle; supported: " + ", ".join(CYCLES))
    return f"https://wwwn.cdc.gov/Nchs/Data/Nhanes/Public/{CYCLES[cycle]}/DataFiles/"


def _file_ids(files):
    if not files or isinstance(files, str):
        raise ValueError("Select at least one file identifier, without an extension")
    if any(not isinstance(name, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", name) for name in files):
        raise ValueError("Use official uppercase file identifiers, e.g. DEMO_J; URLs and paths are not accepted")
    if len(set(files)) != len(files):
        raise ValueError("Duplicate NHANES file identifiers")
    return list(files)


def _official_url(url, base):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc.lower() != "wwwn.cdc.gov" or parsed.query or parsed.fragment:
        raise ValueError("NHANES downloads require the official HTTPS host")
    if not parsed.path.lower().startswith(urlsplit(base).path.lower()):
        raise ValueError("NHANES redirect left the selected release directory")


class _OfficialRedirect(HTTPRedirectHandler):
    def __init__(self, base):
        self.base = base

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _official_url(newurl, self.base)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _Codebook(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts, self.headings, self.heading = [], [], None

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "h2":
            self.heading = []

    def handle_endtag(self, tag):
        if tag.lower() == "h2" and self.heading is not None:
            self.headings.append("".join(self.heading))
            self.heading = None

    def handle_data(self, data):
        self.parts.append(data)
        if self.heading is not None:
            self.heading.append(data)


def _content(data, filename, cycle):
    if filename.endswith(".xpt"):
        if len(data) < 240 or len(data) % 80 or not data.startswith(b"HEADER RECORD*******LIBRARY HEADER RECORD"):
            raise ValueError("Expected a complete SAS XPORT envelope, not an HTML response: " + filename)
    else:
        parsed = _Codebook()
        parsed.feed(data.decode("utf-8-sig", errors="replace"))
        text = " ".join(parsed.parts)
        years = cycle.split("-")
        heading_ok = any("Data Documentation" in heading and re.findall(r"\b(?:19|20)\d{2}\b", heading) == years for heading in parsed.headings)
        if filename[:-4] + ".xpt" not in text or "Codebook" not in text or not heading_ok:
            raise ValueError("Expected the matching NHANES codebook and cycle, not an error page: " + filename)


def _fetch(url, base, timeout):
    opener = build_opener(_OfficialRedirect(base))
    for attempt in range(3):
        try:
            with opener.open(Request(url, headers={"User-Agent": "tametools-nhanes/1.0"}), timeout=timeout) as response:
                _official_url(response.url, base)
                advertised = response.headers.get("Content-Length")
                if advertised is not None and int(advertised) > MAX_BYTES:
                    raise ValueError("NHANES response exceeds the 128 MiB limit")
                data = response.read(MAX_BYTES + 1)
                if len(data) > MAX_BYTES:
                    raise ValueError("NHANES response exceeds the 128 MiB limit")
                if advertised is not None and len(data) != int(advertised):
                    raise ValueError("Incomplete NHANES response")
                return data, response.url
        except (HTTPError, URLError, TimeoutError) as exc:
            retry = not isinstance(exc, HTTPError) or exc.code in {429, 500, 502, 503, 504}
            if not retry or attempt == 2:
                raise ValueError(f"NHANES download failed: {url}: {exc}") from exc
            time.sleep(attempt + 1)


def _json_atomic(path, data):
    fd, name = tempfile.mkstemp(prefix=".nhanes-manifest-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2, ensure_ascii=True)
            stream.write("\n")
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def _manifest(path, cycle):
    if path.is_symlink():
        raise ValueError("Do not use a symlink for the download manifest")
    if not path.exists():
        return dict(version=1, provider="NHANES", cycle=cycle, data_use_url=DATA_USE_URL, files={})
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("version") != 1 or data.get("provider") != "NHANES" or data.get("cycle") != cycle or not isinstance(data.get("files"), dict):
        raise ValueError("Download manifest version/provider/cycle does not match")
    for name, entry in data["files"].items():
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}\.(xpt|htm)", name) or not isinstance(entry, dict):
            raise ValueError("Invalid source manifest entry")
        if not re.fullmatch(r"[0-9a-f]{64}", str(entry.get("sha256", ""))) or type(entry.get("bytes")) is not int or entry["bytes"] <= 0:
            raise ValueError("Invalid source manifest hash/size")
        if entry.get("url") != _base(cycle) + name:
            raise ValueError("Source manifest URL does not match the selected file")
    return data


def download_nhanes(directory, *, cycle, files, offline=False, expected_manifest=None, timeout=45, progress=None):
    """Download XPT/codebook pairs, or verify cached pairs without replacing them.

    A locally recorded SHA-256 detects subsequent changes; it is not a signature
    published by CDC. No weights, eligibility, units or cross-cycle pooling are
    inferred by downloading a file.
    """
    base, files = _base(cycle), _file_ids(files)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 1 <= timeout <= 300:
        raise ValueError("Timeout must be between 1 and 300 seconds")
    expected = {}
    if expected_manifest is not None:
        expected = json.loads(Path(expected_manifest).read_text(encoding="utf-8"))
        if not isinstance(expected, dict):
            raise ValueError("Expected manifest must be a filename-to-SHA256 table")
        for file_id in files:
            for ext in ("xpt", "htm"):
                if not re.fullmatch(r"[0-9a-f]{64}", str(expected.get(file_id + "." + ext, ""))):
                    raise ValueError("Expected manifest must pin every selected XPT and codebook")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    lock = directory / ".nhanes-download.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ValueError("Download directory is locked; check for another downloader before removing a stale lock") from exc
    os.close(fd)
    try:
        raw, path = directory / "sources", directory / "source_manifest.json"
        if raw.is_symlink():
            raise ValueError("Do not use a symlink for the source directory")
        raw.mkdir(exist_ok=True)
        manifest = _manifest(path, cycle)
        results = []
        for file_id in files:
            for ext in ("xpt", "htm"):
                name = file_id + "." + ext
                target, old = raw / name, manifest["files"].get(name)
                if target.is_symlink():
                    raise ValueError("Do not use symlinked source files: " + name)
                if target.exists():
                    if old is None:
                        raise ValueError("Untracked source file exists; choose a new directory: " + name)
                    data = target.read_bytes()
                    digest = hashlib.sha256(data).hexdigest()
                    if digest != old["sha256"] or len(data) != old["bytes"]:
                        raise ValueError("Cached source checksum/size mismatch: " + name)
                    status = "verified_cache"
                else:
                    if offline:
                        raise ValueError("Offline source is missing: " + name)
                    data, resolved = _fetch(base + name, base, timeout)
                    digest = hashlib.sha256(data).hexdigest()
                    if old is not None and (digest != old["sha256"] or len(data) != old["bytes"]):
                        raise ValueError("Remote source changed from the recorded snapshot: " + name)
                    status = "downloaded"
                _content(data, name, cycle)
                if name in expected and digest != expected[name]:
                    raise ValueError("Source differs from the explicitly pinned snapshot: " + name)
                if status == "downloaded":
                    entry = dict(url=base+name, resolved_url=resolved, retrieved_utc=datetime.now(timezone.utc).isoformat(),
                                 bytes=len(data), sha256=digest)
                    # Exclusive creation preserves unrelated files even if a second
                    # program ignores the directory lock.
                    with target.open("xb") as stream:
                        stream.write(data)
                    manifest["files"][name] = entry
                    try:
                        _json_atomic(path, manifest)
                    except Exception:
                        target.unlink()
                        raise
                results.append(dict(file=name, status=status, bytes=len(data), sha256=digest))
                if progress is not None:
                    progress(f"{status}: {name} ({len(data):,} bytes)")
        return dict(cycle=cycle, manifest=str(path), data_use_url=DATA_USE_URL, files=results)
    finally:
        lock.unlink(missing_ok=True)
