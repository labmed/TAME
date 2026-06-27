"""감사·계측 로그와 재현성 유틸리티.

전처리/분석 작업의 단계 수, 소요시간, 입출력 콘텐츠 해시, 이슈 수를 기록하여
(1) 데이터 무결성 감사추적과 (2) 엑셀/스크립트 대비 정량 비교(단계·시간·오류·재현성)를
도구가 스스로 계측·재현 가능하게 만든다.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field, asdict
import hashlib
import json
import time
from pathlib import Path
from typing import Any, Callable, Iterator

from .models import TameDataset

__all__ = [
    "dataset_content_hash",
    "content_hash",
    "StepRecord",
    "RunRecorder",
    "run_twice",
    "verify_reproducible",
    "stamp_integrity",
    "check_integrity",
]


def content_hash(value: Any) -> str:
    """임의 값의 안정적 SHA-256(앞 16자리). TameDataset 은 데이터 콘텐츠 기준으로 해시한다."""
    if isinstance(value, TameDataset):
        return dataset_content_hash(value)
    h = hashlib.sha256()
    h.update(repr(value).encode("utf-8"))
    return h.hexdigest()[:16]


def dataset_content_hash(dataset: TameDataset) -> str:
    """데이터 콘텐츠(태그 헤더 + 셀 값) 기준의 안정적 해시.

    META.LOG 의 타임스탬프처럼 실행마다 달라지는 부분은 제외하므로, 같은 입력에 같은 연산을
    적용하면 항상 같은 해시가 나온다(재현성 판정에 사용).
    """
    h = hashlib.sha256()
    h.update("\t".join(dataset.tagged_headers()).encode("utf-8"))
    h.update(b"\n")
    for row in dataset.df.itertuples(index=False, name=None):
        cells = ["" if cell is None else str(cell) for cell in row]
        h.update("\t".join(cells).encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()[:16]


@dataclass
class StepRecord:
    step: str
    operation: str
    input_hash: str = ""
    output_hash: str = ""
    duration_s: float = 0.0
    issues: int = 0
    warnings: int = 0


@dataclass
class RunRecorder:
    """한 번의 작업 흐름에서 단계별 메트릭을 기록한다.

    사용 예::

        rec = RunRecorder("TAME:T2")
        with rec.step("harmonize", input_ds=ds) as s:
            out = harmonize(ds)
            s["output"] = out
            s["issues"] = len(out.warnings or [])
        print(rec.summary())   # {steps, total_seconds, issues, final_hash, ...}
    """

    label: str = ""
    records: list[StepRecord] = field(default_factory=list)

    @contextmanager
    def step(self, step: str, operation: str = "", *, input_ds: TameDataset | None = None) -> Iterator[dict[str, Any]]:
        holder: dict[str, Any] = {"output": None, "issues": 0, "warnings": 0}
        in_hash = dataset_content_hash(input_ds) if isinstance(input_ds, TameDataset) else ""
        start = time.perf_counter()
        try:
            yield holder
        finally:
            duration = time.perf_counter() - start
            output = holder.get("output")
            if isinstance(output, TameDataset):
                out_hash = dataset_content_hash(output)
            elif output is None:
                out_hash = ""
            else:
                out_hash = content_hash(output)
            self.records.append(
                StepRecord(
                    step=step,
                    operation=operation or step,
                    input_hash=in_hash,
                    output_hash=out_hash,
                    duration_s=round(duration, 6),
                    issues=int(holder.get("issues", 0) or 0),
                    warnings=int(holder.get("warnings", 0) or 0),
                )
            )

    def summary(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "steps": len(self.records),
            "total_seconds": round(sum(r.duration_s for r in self.records), 6),
            "issues": sum(r.issues for r in self.records),
            "warnings": sum(r.warnings for r in self.records),
            "final_hash": self.records[-1].output_hash if self.records else "",
        }

    def to_jsonl(self) -> str:
        lines = [json.dumps({"label": self.label, **asdict(r)}, ensure_ascii=False) for r in self.records]
        return "\n".join(lines)

    def write_jsonl(self, path: str | Path) -> None:
        text = self.to_jsonl()
        Path(path).write_text(text + ("\n" if text else ""), encoding="utf-8")


def run_twice(run_callable: Callable[[], Any]) -> dict[str, Any]:
    """동일 연산을 두 번 실행하고 콘텐츠 해시 일치 여부로 재현성을 판정한다."""
    first = run_callable()
    second = run_callable()
    h1 = content_hash(first)
    h2 = content_hash(second)
    return {"hash_first": h1, "hash_second": h2, "reproducible": h1 == h2}


def verify_reproducible(run_callable: Callable[[], Any], *, runs: int = 2) -> dict[str, Any]:
    """``runs`` 회 실행한 결과 해시가 모두 같은지 검증한다."""
    hashes = [content_hash(run_callable()) for _ in range(max(2, runs))]
    return {"hashes": hashes, "reproducible": len(set(hashes)) == 1, "runs": len(hashes)}


def stamp_integrity(dataset: TameDataset) -> TameDataset:
    """현재 데이터 콘텐츠 해시를 META.INTEGRITY 에 새겨 변조탐지(tamper-evident) 기준점을 만든다.

    콘텐츠 해시는 태그 헤더 + 셀 값만으로 계산하므로(META 제외), 도장을 META 에 적어도 해시는 변하지
    않는다. 이후 셀이 바뀌면 재계산 해시가 달라져 check_integrity 가 적발한다.
    """
    from .toml_compat import dumps as dumps_toml

    digest = dataset_content_hash(dataset)
    meta = dict(dataset.meta)
    meta["INTEGRITY"] = {"CONTENT_HASH": digest, "ALGO": "sha256-16"}
    raw = dict(dataset.raw_sections)
    raw["META"] = dumps_toml(meta)
    return dataset.replace(meta=meta, raw_sections=raw)


def check_integrity(dataset: TameDataset) -> dict[str, Any]:
    """저장된 META.INTEGRITY.CONTENT_HASH 와 현재 콘텐츠 해시를 비교해 무결성을 판정한다."""
    integrity = dataset.meta.get("INTEGRITY")
    stored = integrity.get("CONTENT_HASH") if isinstance(integrity, dict) else None
    recomputed = dataset_content_hash(dataset)
    return {
        "present": stored is not None,
        "stored": stored,
        "recomputed": recomputed,
        "intact": bool(stored is not None and stored == recomputed),
    }
