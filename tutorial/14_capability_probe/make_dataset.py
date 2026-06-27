"""실무형 임상화학 데이터셋 생성기 — 20개 예제 실행용 단일 데이터셋.

생성 컬럼은 실제 LIS 추출본을 모사한다:
환자ID, 검체번호, 성별, 생년월일, 나이, 채취/접수/보고 일시,
검사항목, 보고값(<NUM>), 단위, 참고치 하한/상한, 장비, 기관, 진료과.
환자는 여러 날짜에 반복 방문(델타체크용), 장비는 2종(방법비교용),
일부 결측/중복/이상치/dirty 카테고리를 의도적으로 포함한다.
"""
from __future__ import annotations
import random
from datetime import datetime, timedelta

random.seed(20260624)

ITEMS = {
    # name: (ref_low, ref_high, unit, mean, sd)
    "AST": (0, 40, "U/L", 25, 12),
    "ALT": (0, 41, "U/L", 26, 18),
    "GGT": (0, 60, "U/L", 30, 25),
    "ALP": (40, 130, "U/L", 85, 24),
    "TP":  (6.0, 8.0, "g/dL", 7.1, 0.45),
    "Cr":  (0.7, 1.3, "mg/dL", 1.0, 0.3),
}
SEX_RAW = ["M", "F", "Male", "Female", "남", "여"]
INSTR = ["test_analyzer_2", "test_analyzer_1"]
HOSP = ["test_hospital_1", "test_hospital_2"]
DEPT_RAW = ["IM", "내과", "GS", "외과", "EM", "응급의학과"]

rows = []
start = datetime(2026, 1, 5, 8, 0)
pid = 0
for p in range(120):
    pid += 1
    patient = f"P{pid:04d}"
    sex = random.choice(SEX_RAW)
    birth = datetime(random.randint(1945, 2005), random.randint(1, 12), random.randint(1, 28))
    visits = random.choice([1, 1, 2, 3])  # some patients repeat -> delta check
    for v in range(visits):
        day = start + timedelta(days=random.randint(0, 120), hours=random.randint(0, 10))
        received = day + timedelta(minutes=random.randint(5, 40))
        resulted = received + timedelta(minutes=random.randint(20, 180))
        instr = random.choice(INSTR)
        hosp = random.choice(HOSP)
        dept = random.choice(DEPT_RAW)
        specimen = f"S{day.strftime('%y%m%d')}{random.randint(100,999)}"
        age = day.year - birth.year
        for item, (lo, hi, unit, mu, sd) in ITEMS.items():
            val = max(0.1, random.gauss(mu, sd))
            # comparator-aware low values
            disp = f"{val:.1f}"
            if item in ("AST", "ALT", "GGT") and val < 8:
                disp = f"<{int(lo) if lo else 8}"
            # inject a few gross outliers
            if random.random() < 0.01:
                disp = f"{val*8:.1f}"
            rows.append({
                "등록번호": patient, "검체번호": specimen, "성별": sex,
                "생년월일": birth.strftime("%Y-%m-%d"), "나이": str(age),
                "채취일시": day.strftime("%Y-%m-%d %H:%M:%S"),
                "접수일시": received.strftime("%Y-%m-%d %H:%M:%S"),
                "보고일시": resulted.strftime("%Y-%m-%d %H:%M:%S"),
                "검사항목명": item, "보고값": disp, "단위": unit,
                "참고치하한": str(lo), "참고치상한": str(hi),
                "장비": instr, "수집기관": hosp, "진료과": dept,
            })

# inject ~15 exact duplicate rows
for _ in range(15):
    rows.append(dict(random.choice(rows)))
# inject ~20 missing-result rows
for _ in range(20):
    r = dict(random.choice(rows)); r["보고값"] = ""; rows.append(r)

random.shuffle(rows)

headers = [
    ("등록번호", "ID(patient)::STR"), ("검체번호", "ID(sample)::STR"), ("성별", "SEX::CATEGORY"),
    ("생년월일", "BIRTHDATE::DATE"), ("나이", "AGE(baseline)::NUM"),
    ("채취일시", "COLLECTION_AT::DATETIME"),
    ("접수일시", "RECEIVED_AT::DATETIME"),
    ("보고일시", "RESULT_TIME::DATETIME"),
    ("검사항목명", "ITEM::TESTNAME::CATEGORY"), ("보고값", "RESULT::<NUM>"), ("단위", "UNIT"),
    ("참고치하한", "REF_LOW::NUM"), ("참고치상한", "REF_HIGH::NUM"),
    ("장비", "INSTRUMENT::CATEGORY"), ("수집기관", "GROUP::BY::CATEGORY"), ("진료과", "WARD::CATEGORY"),
]
col_names = [h[0] for h in headers]
header_line = "\t".join(f"[[{tag}]]{name}" for name, tag in headers)

lines = ["<META>", "[INFO]", 'DESCRIPTION = "실무형 임상화학 검사 데이터 (20개 예제 실행용)"',
         "", "[SETTINGS]", 'VALIDATE_ERROR = "DELETE"', 'CRR = "VALUE"',
         "", "[WORKS]", 'DEFAULT = ["VALIDATE", "DESCRIBE", "EDA"]', "</META>", "<DATA>", header_line]
for r in rows:
    lines.append("\t".join(str(r[c]) for c in col_names))
lines.append("</DATA>")

with open("tutorial/14_capability_probe/clinical_chem.tame", "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")
print(f"rows={len(rows)} cols={len(headers)}")
