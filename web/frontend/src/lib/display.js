export function displayValue(value, numeric = true) {
  if (value === null || value === undefined) return '—';
  const text = String(value);
  const states = {'<<ABSENT>>': '자료 없음', '<<NULL>>': '결측', '<<EMPTY>>': '빈 문자열'};
  if (states[text]) return states[text];
  if (/^<<WS:/.test(text)) return '공백';
  if (!numeric || text.trim() === '' || !/^[+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?$/i.test(text)) return text;
  const number = Number(text);
  if (!Number.isFinite(number)) return text;
  if (number !== 0 && Math.abs(number) < 0.0001) return number.toExponential(3);
  return new Intl.NumberFormat('ko-KR', {maximumFractionDigits: 4}).format(number);
}

export function interval(low, high) {
  const missing = value => value === null || value === undefined || String(value).startsWith('<<');
  if (missing(low) || missing(high)) return '산출 안 됨';
  return `${displayValue(low)} – ${displayValue(high)}`;
}

export const statusLabels = {
  not_evaluable: '산출 불가 · 사유 확인', degenerate_distribution: '동일값 분포 · 검토 필요', insufficient_n: '표본 부족', insufficient_order_resolution: '꼬리 분위수 산출에 표본 부족',
  exploratory_only: '탐색적 후보 구간', small_sample_review: '소표본 검토 필요',
  candidate_requires_verification: '지역 검증 필요', censoring_sensitivity_only: '검열값 처리 영향 검토',
  ci_review: '신뢰구간 정밀도 검토', distribution_review: '분포 검토 필요',
};

export function referenceNote(row, omit = []) {
  if (row.status === 'insufficient_n') return `유효 표본 ${row.n}개로 계산에 필요한 최소 표본 수를 충족하지 못했습니다. 추가 참고인이 필요합니다.`;
  if (row.status === 'insufficient_order_resolution') return `유효 표본 ${row.n}개로 요청한 꼬리 분위수의 순위를 결정할 수 없습니다. 포함률과 방법을 검토하고 표본을 확보하세요.`;
  const translations = {
    'Reference-population suitability not confirmed; these are sample-based candidate limits':'참고인 집단의 적절성이 확인되지 않은 탐색적 후보 구간입니다.',
    'n counts rows; independent individuals have not been established':'n은 행 수입니다. 독립된 개인 수인지 확인해야 합니다.',
    'Censored observations were excluded or substituted; tails may be biased':'검출한계값을 제외·대체하여 꼬리 추정에 편향이 생길 수 있습니다.',
    'Reference-limit CI unavailable; do not treat precision as established':'참고한계 신뢰구간을 계산하지 못해 정밀도를 확인할 수 없습니다.',
    'CI width exceeds the configured fraction of RI width (review threshold, not a universal CLSI rule)':'신뢰구간 폭이 설정한 경고 기준을 넘습니다. 일률적인 CLSI 기준이 아니며 정밀도 검토가 필요합니다.',
    'Flagged outlier fraction exceeds OUTLIER_WARN_RATE; review exclusions and sensitivity':'이상치 표시 비율이 설정 기준을 넘습니다. 제외 타당성과 민감도를 검토하세요.',
    'Distribution on the fitted scale needs review; inspect histogram and Q-Q plot':'계산 척도의 분포를 검토하세요. 히스토그램과 Q-Q 도표를 확인하세요.',
    'Negative lower limit from a nonnegative sample: check physical plausibility and the fitted distribution; no automatic zero clamp':'음수 하한의 물리적 타당성과 분포 적합성을 검토하세요. 하한을 임의로 0으로 자르지 않았습니다.',
    'Zero-width interval does not establish a usable reference interval':'폭이 0인 구간은 유효한 참고구간의 근거가 되지 않습니다.'
  };
  let note = row.notes || '';
  for (const common of omit) note = note.replaceAll(common, '');
  note = note.replace(/^;\s*|;\s*$/g, '').replace(/;\s*;/g, ';');
  for (const [source,target] of Object.entries(translations)) note = note.replaceAll(source,target);
  return note.replace(/n<(\d+) per partition; method choice does not remove small-sample uncertainty/g,'집단별 n<$1입니다. 방법을 바꾸어도 소표본 불확실성이 사라지지 않습니다.');
}

export function groupLabel(value) {
  if (value === 'ALL') return '전체';
  try {
    return Object.entries(JSON.parse(value)).map(([k,v]) => `${k === 'SEX' ? '성별' : k === 'AGE' ? '연령' : k}: ${{male:'남성',female:'여성'}[v] || v}`).join(' · ');
  } catch {return value;}
}
export const methodLabels = {NONPARAMETRIC:'비모수', PARAMETRIC:'모수', LOG_PARAMETRIC:'로그 모수', ROBUST:'Robust', LOG_ROBUST:'로그 Robust', BOXCOX_PARAMETRIC:'Box–Cox 모수', BOXCOX_SHIFTED_PARAMETRIC:'원점 이동 Box–Cox 모수', BOXCOX_ROBUST:'Box–Cox Robust'};

export function displayUnit(unit) {
  return displayValue(unit, false).replaceAll('umol/L', 'µmol/L').replaceAll('ug/L', 'µg/L');
}
export function testLabel(row, labels = {}) {
  return row.test_name === row.result_id ? labels?.[row.result_id] || row.test_name : row.test_name;
}
export function commonReferenceNotes(rows) {
  // Only these complete messages are moved; semicolons inside a message matter.
  const notes = [
    'Reference-population suitability not confirmed; these are sample-based candidate limits',
    'n counts rows; independent individuals have not been established'
  ];
  return rows.length ? notes.filter(note => rows.every(row => String(row.notes || '').includes(note))) : [];
}
export function settingsSummary(cfg = {}, rows = []) {
  const used = rows.map(r => Number(r.n)).filter(Number.isFinite);
  const partitions = (cfg.PARTITION_BY || []).map(v => ({SEX:'성별', AGE:'연령'}[v] || v));
  return [
    `방법: ${(cfg.METHODS?.length ? cfg.METHODS : [cfg.METHOD]).map(v=>methodLabels[v] || v).join(', ')}`,
    `포함률 ${displayValue(cfg.COVERAGE * 100)}% · CI ${displayValue(cfg.CI_LEVEL * 100)}%`,
    `이상치 ${cfg.OUTLIER_METHOD} / ${{FLAG:'표시·유지',REMOVE:'계산에서 제외'}[cfg.OUTLIER_ACTION] || cfg.OUTLIER_ACTION}`,
    `이상치 경고 ${displayValue(cfg.OUTLIER_WARN_RATE * 100)}%`,
    `검열값 ${{ERROR:'중단',EXCLUDE:'계산에서 제외',VALUE:'경계값',RELEASED:'공개 대체값'}[cfg.CENSOR_POLICY] || cfg.CENSOR_POLICY}`,
    `분할 ${partitions.join('·') || '없음'}`,
    used.length ? `유효 n ${Math.min(...used)}–${Math.max(...used)} / 집단별` : ''
  ].filter(Boolean).join(' | ');
}
