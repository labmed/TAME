<script>
  import { createEventDispatcher } from 'svelte';
  import { previewSexNormalization } from './api.js';
  export let dataset;
  export let busy = false;
  const dispatch = createEventDispatcher();
  let sexColumn = '';
  let sourceColumn = '';
  let outputName = '';
  let rows = [];
  let preview = null;
  let loading = false;
  let error = '';
  let acknowledged = false;
  let key = '';
  const labels = {male:'남성 (male)', female:'여성 (female)', other:'기타 (other)', unknown:'미상 (unknown)'};
  $: columns = dataset?.columns || [];
  $: identity = `${dataset?.filename}:${JSON.stringify(dataset?.sexNormalization || {})}:${columns.map(c => `${c.name}:${c.metadata?.ID || ''}`).join('|')}`;
  $: if (dataset && key !== identity) {
    key = identity;
    const saved = dataset.sexNormalization || {};
    sexColumn = (saved.SEX_ID && columns.find(c => c.metadata?.ID === saved.SEX_ID)?.name) || columns.find(c => c.tags.includes('SEX'))?.name || '';
    sourceColumn = (saved.SOURCE_ID && columns.find(c => c.metadata?.ID === saved.SOURCE_ID)?.name) || columns.find(c => c.tags.some(t => ['SOURCE','SHEET'].includes(t)))?.name || '';
    outputName = (saved.OUTPUT_ID && columns.find(c => c.metadata?.ID === saved.OUTPUT_ID)?.name) || saved.OUTPUT_NAME || `${sexColumn || 'Sex'}_standard`;
    rows = []; preview = null; acknowledged = false; error = '';
  }
  $: unmapped = rows.filter(r => !r.target || r.missingSource).reduce((n,r) => n+r.count, 0);
  $: totals = Object.keys(labels).map(value => ({value, count:rows.filter(r => r.target === value).reduce((n,r)=>n+r.count,0)}));
  function invalidate() {rows = []; preview = null; acknowledged = false; error = '';}
  async function inspect() {
    loading = true; error = ''; acknowledged = false;
    try {
      const saved = dataset.sexNormalization || {};
      const sameBinding = Boolean(saved.SEX_ID) && columns.find(c => c.name === sexColumn)?.metadata?.ID === saved.SEX_ID
        && (columns.find(c => c.name === sourceColumn)?.metadata?.ID || '') === (saved.SOURCE_ID || '');
      const result = await previewSexNormalization(dataset, {sexColumn, sourceColumn, outputName,
        mappings:sameBinding ? saved.MAPPINGS : []});
      preview = result;
      rows = result.rows.map(row => ({...row, target:row.target || ''}));
    } catch (e) {error = e.message;}
    finally {loading = false;}
  }
  function apply() {
    const mappings = new Map();
    for (const row of rows) {
      if (!mappings.has(row.source)) mappings.set(row.source, Object.create(null));
      mappings.get(row.source)[row.code] = row.target;
    }
    dispatch('run', {sexColumn, sourceColumn, outputName,
      mappings:[...mappings].map(([source,values])=>({source,values}))});
  }
</script>

<section class="reference-panel sex-normalization-panel">
  <h2>출처별 성별 정규화</h2>
  <p>입력: <strong>{dataset?.filename}</strong></p>
  <p class="context-notice">각 데이터셋의 코드 설명서를 확인해 지정하세요. 예: A 자료의 0=여성·1=남성, B 자료의 1=여성·2=남성. 같은 1도 출처에 따라 다르게 변환합니다. 숫자 코드의 의미는 자동 추정하지 않습니다.</p>
  <fieldset disabled={busy || loading}>
    <legend>열 연결</legend>
    <div class="settings-grid">
      <label>원 성별 열<select bind:value={sexColumn} on:change={invalidate}><option value="">열을 선택하세요</option>{#each columns as c}<option value={c.name}>{c.name}</option>{/each}</select></label>
      <label>데이터셋 출처 열<select bind:value={sourceColumn} on:change={invalidate}><option value="">출처 구분 없음 · 공통 규칙</option>{#each columns.filter(c => c.name !== sexColumn) as c}<option value={c.name}>{c.name}</option>{/each}</select></label>
      <label>표준 성별 열 이름<input bind:value={outputName} on:input={() => acknowledged = false} /></label>
    </div>
    <p>Excel 시트를 병합했다면 시트명 열을, 이미 합쳐진 자료라면 원 데이터셋을 구분하는 열을 선택하세요. 출처가 사라진 자료에서는 서로 다른 숫자 코딩을 구분할 수 없습니다.</p>
    <button on:click={inspect} disabled={!sexColumn}>출처별 코드 확인</button>
  </fieldset>
  {#if loading}<p role="status">출처와 원 코드를 확인하고 있습니다…</p>{/if}
  {#if error}<p role="alert">{error}</p>{/if}
  {#if preview}
    <h3>코드표 설정과 변환 미리보기</h3>
    <p>{preview.rowCount.toLocaleString()}행 · 원 성별 결측 {preview.missingSex.toLocaleString()}행은 결측 상태로 보존 · 미지정 {unmapped.toLocaleString()}행</p>
    <div class="table-wrap"><table><thead><tr><th>출처</th><th>원 코드</th><th>행 수</th><th>표준 성별</th></tr></thead><tbody>
      {#each rows as row}<tr><td>{row.missingSource ? '출처 결측 · 수정 필요' : row.source || '전체 자료'}</td><td>{row.code}</td><td>{row.count.toLocaleString()}</td><td><select aria-label={`${row.source || '전체 자료'} / ${row.code} 표준 성별`} bind:value={row.target} on:change={() => acknowledged = false} disabled={busy || row.missingSource}><option value="">의미를 선택하세요</option>{#each Object.entries(labels) as [value,label]}<option {value}>{label}</option>{/each}</select></td></tr>{/each}
    </tbody></table></div>
    <p class="sex-preview-totals">변환 후: {totals.map(t => `${labels[t.value]} ${t.count.toLocaleString()}행`).join(' · ')}</p>
    <p>원 성별·출처 값과 행을 보존합니다. 새 표준 열에 SEX 역할을 부여하고 EP28의 성별 입력에 연결하며, 코드표와 열 ID를 META·LOG에 기록합니다. 별도로 작성한 저장 분석 계획의 열 ID는 검토해 주세요.</p>
    <label class="check-row"><input type="checkbox" bind:checked={acknowledged} disabled={busy || unmapped > 0} />출처별 코드의 의미와 변환 후 인원을 확인했습니다.</label>
    <button class="primary" on:click={apply} disabled={busy || unmapped > 0 || !acknowledged || !outputName.trim()}>표준 성별 열 생성</button>
  {/if}
</section>
