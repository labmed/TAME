<script>
  import { createEventDispatcher } from 'svelte';
  export let dataset;
  export let busy = false;
  const dispatch = createEventDispatcher();
  let sourceKey = '';
  let selected = [];
  let method = 'NONPARAMETRIC';
  let coverage = 0.95;
  let confidence = 0.9;
  let outlier = 'TUKEY';
  let action = 'FLAG';
  let censor = 'ERROR';
  let sex = false;
  let age = false;
  let ageCuts = '0, 20, 40, 60, 100';
  let minN = 120;
  let bootstrap = 2000;
  let outlierWarnPercent = 2;
  let acknowledged = false;
  $: results = (dataset?.columns || []).filter(c => c.tags.includes('RESULT'));
  $: sexColumn = (dataset?.columns || []).find(c => c.tags.includes('SEX'));
  $: ageColumn = (dataset?.columns || []).find(c => c.tags.some(t => t === 'AGE' || t.startsWith('AGE(') || t === 'RAW_AGE'));
  $: topCoded = ageColumn?.metadata?.TOP_CODE_VALUE !== undefined;
  $: key = `${dataset?.filename}:${JSON.stringify(dataset?.referenceSettings || {})}`;
  $: if (key !== sourceKey) {
    sourceKey = key;
    const cfg = dataset?.referenceSettings || {};
    selected = cfg.RESULT_IDS?.length ? [...cfg.RESULT_IDS] : results.map(c => c.metadata?.ID || c.name);
    method = cfg.METHOD || 'NONPARAMETRIC'; coverage = cfg.COVERAGE || .95; confidence = cfg.CI_LEVEL || .9;
    outlier = cfg.OUTLIER_METHOD || 'TUKEY'; action = cfg.OUTLIER_ACTION || 'FLAG';
    censor = cfg.CENSOR_POLICY || 'ERROR';
    sex = cfg.PARTITION_BY ? cfg.PARTITION_BY.includes('SEX') : Boolean(sexColumn);
    age = !topCoded && Boolean(cfg.PARTITION_BY?.includes('AGE'));
    outlierWarnPercent = (cfg.OUTLIER_WARN_RATE ?? .02) * 100;
    minN = cfg.MIN_N || 120; bootstrap = cfg.BOOTSTRAP_N || 2000; acknowledged = false;
    if (cfg.AGE_CUTS?.length) ageCuts = cfg.AGE_CUTS.join(', ');
  }
  function run() {
    const partitions = [...(sex ? ['SEX'] : []), ...(age ? ['AGE'] : [])];
    const options = {RESULT_IDS:selected, METHOD:method, METHODS:[], COVERAGE:Number(coverage), CI_LEVEL:Number(confidence),
      QUANTILE_TYPE:6, CI_METHOD:'AUTO', OUTLIER_METHOD:outlier, OUTLIER_ACTION:action, CENSOR_POLICY:censor,
      PARTITION_BY:partitions, OUTLIER_WARN_RATE:Number(outlierWarnPercent)/100, MIN_N:Number(minN), BOOTSTRAP_N:Number(bootstrap),
      ...(sex && sexColumn ? {SEX_ID:sexColumn.metadata?.ID || sexColumn.name} : {}),
      ...(age && ageColumn ? {AGE_ID:ageColumn.metadata?.ID || ageColumn.name, AGE_CUTS:ageCuts.split(',').map(v=>Number(v.trim()))} : {})};
    dispatch('run',options);
  }
</script>

<section class="reference-panel">
  <h2>EP28 참고구간 설정</h2>
  <p>입력: <strong>{dataset?.filename || '입력 자료를 선택해 주세요'}</strong></p>
  <p class="context-notice">원자료와 단위·방법·처리 정책을 함께 검토합니다. 계산 결과는 후보 구간이며 건강한 참고인 선정과 검사실 검증을 대신하지 않습니다.</p>
  {#if !results.length}<p role="alert">RESULT 열이 없습니다. 검사값 열의 태그를 먼저 지정해 주세요.</p>{/if}
  <fieldset><legend>검사값 열</legend><div class="check-grid">{#each results as column}<label><input type="checkbox" bind:group={selected} value={column.metadata?.ID || column.name} />{column.metadata?.LABEL || column.name}<small>{column.metadata?.UNIT || '단위 열 바인딩 확인'}</small></label>{/each}</div></fieldset>
  <div class="settings-grid">
    <label>산출 방법<select bind:value={method}><option value="NONPARAMETRIC">비모수 · type 6</option><option value="PARAMETRIC">모수</option><option value="LOG_PARAMETRIC">로그 모수</option><option value="ROBUST">Robust</option><option value="LOG_ROBUST">로그 Robust</option><option value="BOXCOX_PARAMETRIC">Box–Cox 모수</option><option value="BOXCOX_SHIFTED_PARAMETRIC">원점 이동 Box–Cox 모수</option><option value="BOXCOX_ROBUST">Box–Cox Robust</option></select></label>
    <label>참고구간 포함률<select bind:value={coverage}><option value={.9}>90%</option><option value={.95}>95%</option><option value={.99}>99%</option></select></label>
    <label>참고한계 신뢰수준<select bind:value={confidence}><option value={.9}>90%</option><option value={.95}>95%</option><option value={.99}>99%</option></select></label>
    <label>이상치 판정<select bind:value={outlier}><option value="NONE">판정 안 함</option><option value="TUKEY">Tukey · 1.5 IQR</option><option value="LOG_TUKEY">로그 Tukey</option><option value="HORN">Horn</option><option value="DIXON_Q">Dixon Q · α=0.05</option><option value="REED">Reed · D/R</option><option value="COOK">Cook</option></select></label>
    <label>이상치 조치<select bind:value={action}><option value="FLAG">표시만 · 계산에 유지</option><option value="REMOVE">판정된 값을 계산에서 제외</option></select></label>
    <label>검출한계·부등호 처리<select bind:value={censor}><option value="ERROR">미만·초과값이 있으면 중단</option><option value="EXCLUDE">계산에서 제외 · 원자료 보존</option><option value="VALUE">경계값으로 계산 · 원자료 보존</option><option value="RELEASED">공개 대체값 사용 · 바인딩 필요</option></select></label>
    <label>권장 표본 수 경고 기준<input type="number" min="120" max="1000000" bind:value={minN} /></label>
    <label>이상치 표시 비율 경고 기준 (%)<input type="number" min="0" max="100" step="0.1" bind:value={outlierWarnPercent} /><small>기본 2%. 이상치 제거 기준과 별개인 검토용 경고 기준입니다.</small></label>
    <label>Bootstrap 횟수<input type="number" min="50" max="20000" step="50" bind:value={bootstrap} /><small>50회는 과거 연구 재현용입니다. 새 연구는 더 많은 반복에서 안정성을 확인하세요.</small></label>
  </div>
  <fieldset><legend>집단 분할</legend>
    <label class="check-row"><input type="checkbox" bind:checked={sex} disabled={!sexColumn} />성별로 나누어 비교</label>
    <label class="check-row"><input type="checkbox" bind:checked={age} disabled={!ageColumn || topCoded} />연령 구간으로 나누어 비교</label>
    {#if age}<label>연령 경계 · 연 단위, 쉼표 구분<input bind:value={ageCuts} /></label><small>각 구간은 하한 포함·상한 제외입니다. 구간 밖 관측은 분할 결과에서 제외됩니다.</small>{/if}
    {#if !ageColumn}<small>개인별 AGE 열이 없어 연령 분할을 하지 않습니다.</small>{/if}
    {#if topCoded}<small>연령에 상한 코드가 있습니다. 80세 이상을 정확한 80세로 처리하지 않도록, 명시적인 연령 범주를 검토한 뒤 분할해 주세요.</small>{/if}
  </fieldset>
  <p>신뢰구간은 선택한 방법과 표본 수에 따라 순위법·모수법·bootstrap을 적용합니다. 소표본이나 산출 불가 사유는 결과에 표시합니다.</p>
  <label class="check-row"><input type="checkbox" bind:checked={acknowledged} />탐색적 후보 구간이며 임상 채택 전 별도 검토가 필요함을 확인했습니다.</label>
  <button class="primary" on:click={run} disabled={busy || !selected.length || !acknowledged}>설정한 조건으로 참고구간 계산</button>
</section>
