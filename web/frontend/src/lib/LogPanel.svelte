<script>
  import {inspectProvenance, exportProvenance, saveBlob} from './api.js';
  export let dataset = null;
  export let disabled = false;
  let history = null;
  let previous = null;
  let loading = false;
  let error = '';
  let query = '';
  let expanded = {};
  $: if (dataset?.provenance !== previous) {
    previous = dataset?.provenance;
    history = dataset?.provenance || null;
    expanded = {};
    error = '';
  }
  $: groups = (history?.groups || []).filter(g => !query || `${g.label} ${g.summary} ${g.status} ${g.events.map(e => e.OPERATION).join(' ')}`.toLowerCase().includes(query.toLowerCase()));
  const statuses = {SUCCEEDED:'완료',FAILED:'실패',SKIPPED:'건너뜀',CANCELLED:'중단',UNRECORDED:'상태 미기록',PASS:'일치',FAIL:'불일치',PARTIAL:'일부만 확인',NOT_CHECKED:'미검사'};
  const checks = {history:'기록 연결·내용',data:'현재 데이터',controls:'현재 설정',artifacts:'외부 산출물',rerun:'과거 실행 재현'};
  const countLabels = {MAPPED_ROWS:'매핑',MISSING_SEX_PRESERVED:'결측 성별 보존',UNMAPPED_ROWS:'미매핑',ADDED_COLUMNS:'추가 열',OUTLIERS_FLAGGED:'이상치 표시',ISSUES:'검증 이슈'};
  async function verify() {
    loading = true; error = '';
    try {history = await inspectProvenance(dataset);}
    catch(e) {error = e.message;}
    finally {loading = false;}
  }
  async function download() {
    loading = true; error = '';
    try {
      const record = await exportProvenance(dataset);
      saveBlob(new Blob([JSON.stringify(record,null,2)],{type:'application/json;charset=utf-8'}),`${dataset.filename || 'dataset'}.history.json`);
    } catch(e) {error = e.message;}
    finally {loading = false;}
  }
  function timeLabel(value) {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? (value || '시각 미기록') : date.toLocaleString('ko-KR', {timeZoneName:'short'});
  }
</script>

<section class="log-panel" aria-label="분석 이력">
  <div class="heading"><div><h2>분석 이력</h2><p>입력·설정·실행·저장 기록을 단계별로 확인합니다.</p></div>
    <div class="buttons"><button on:click={verify} disabled={!dataset || loading || disabled}>{loading ? '확인 중…' : '현재 값으로 기록 검증'}</button>
    <button on:click={download} disabled={!dataset || loading || disabled}>전체 이력 JSON 저장</button></div></div>
  {#if error}<p role="alert" class="error">{error}</p>{/if}
  {#if !dataset}<p>데이터를 열면 분석 이력이 표시됩니다.</p>
  {:else if history}
    <div class="checks" aria-label="이력 검증 결과">
      {#each Object.entries(checks) as [key,label]}
        <div><span>{label}</span><strong class:fail={history.verification.checks[key] === 'FAIL'} class:pass={history.verification.checks[key] === 'PASS'}>{statuses[history.verification.checks[key]]}</strong></div>
      {/each}
    </div>
    <p class="scope">마지막으로 확인한 값입니다. 데이터·메타를 편집한 뒤에는 다시 검증하세요. 이 검사는 기록된 해시를 대조하며 과거 분석을 자동 재실행하지 않습니다. 외부 산출물은 현재 작업 폴더에서 접근할 수 있을 때 확인합니다.</p>
    {#if history.verification.legacy_events}<p class="notice">구형 기록 {history.verification.legacy_events}개는 원문을 보존했습니다. 당시 기록되지 않은 상태·해시는 검증할 수 없습니다.</p>{/if}
    {#if history.verification.unsuccessful_steps.length}<p class="notice">실패·중단·건너뜀 기록이 {history.verification.unsuccessful_steps.length}개 있습니다. 해당 단계를 확인하세요.</p>{/if}
    {#if history.verification.errors.length || history.verification.missing.length}
      <details open><summary>확인이 필요한 기록</summary><ul>{#each [...history.verification.errors,...history.verification.missing] as issue}<li>{issue}</li>{/each}</ul></details>
    {/if}
    <div class="filter"><label for="log-filter">단계 찾기</label><input id="log-filter" bind:value={query} placeholder="성별, SAMPLE, EP28…" /><span>{history.operationCount}개 작업 · {history.eventCount}개 기록</span></div>
    <p class="scope">입출력 행 수는 저장 자료의 행 수입니다. 분석 결과의 출력 행은 사람 수가 아닌 결과표 행 수일 수 있습니다.</p>
    {#if !history.eventCount}<p>아직 저장된 실행 기록이 없습니다. 이후 실행한 전처리·분석부터 기록됩니다.</p>{/if}
    <ol>
      {#each groups as group (group.id)}
        <li><span class="number">{group.index}</span><article>
          <div class="row"><h3>{group.label}</h3><span class="badge" class:fail={group.status === 'FAILED'}>{statuses[group.status] || group.status}</span></div>
          <p class="small">{timeLabel(group.timestamp)} · tametools {group.version} · {group.events.length}개 기록</p>
          <p>{group.summary}</p>
          <div class="counts"><span>입력 <b>{group.counts.INPUT_ROWS ?? '미기록'}</b>행</span><span>출력 <b>{group.counts.OUTPUT_ROWS ?? '미기록'}</b>행</span>
            {#each Object.entries(countLabels) as [key,label]}{#if group.counts[key] !== undefined}<span>{label} <b>{group.counts[key]}</b></span>{/if}{/each}
          </div>
          {#if group.mappings.length}<div class="mappings"><strong>출처별 코드표</strong>{#each group.mappings as mapping}<p><b>{mapping.SOURCE}</b>: {Object.entries(mapping.VALUES).map(([code,value])=>`${code} → ${value}`).join(', ')}</p>{/each}</div>{/if}
          {#if group.warnings.length}<details><summary>해석·실행 주의사항 {group.warnings.length}개</summary><ul>{#each group.warnings as warning}<li>{warning}</li>{/each}</ul></details>{/if}
          <details on:toggle={event => expanded = {...expanded,[group.id]:event.currentTarget.open}}><summary>적용 설정·입출력·연결 ID 상세</summary>
            {#if expanded[group.id]}{#each group.events as entry}<h4>{entry.OPERATION}</h4><pre>{JSON.stringify(entry,null,2)}</pre>{/each}{/if}
          </details>
        </article></li>
      {/each}
    </ol>
  {/if}
</section>
<style>
  .log-panel{padding:24px;max-width:1250px}.heading,.row,.buttons,.counts,.filter{display:flex;gap:12px;align-items:center;flex-wrap:wrap}.heading,.row{justify-content:space-between}h2,h3,h4{margin:0}h2{font-size:22px}h3{font-size:17px}h4{margin-top:12px}p{line-height:1.6;margin:10px 0}.heading p,.scope,.small{color:#64748b}.scope,.small{font-size:13px}.checks{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin:22px 0 10px}.checks>div{padding:14px;background:#f4f7fb;border:1px solid #dbe3ed;border-radius:8px}.checks span,.checks strong{display:block}.checks span{font-size:13px;color:#617087}.checks strong{margin-top:5px}.pass{color:#176249}.fail,.error{color:#aa2836}.notice{background:#fff5df;border:1px solid #efdbb3;padding:10px 14px;border-radius:7px}.filter{margin-top:25px}.filter span{font-size:13px;color:#64748b}ol{list-style:none;margin:18px 0;padding:0}li{overflow-wrap:anywhere}ol>li{display:flex;gap:12px;margin-bottom:15px}.number{width:30px;height:30px;flex:0 0 30px;border-radius:50%;background:#e2ebf6;text-align:center;line-height:30px;margin-top:15px;font-weight:bold;color:#31557e}article{width:calc(100% - 42px);border:1px solid #dbe3ed;padding:18px;border-radius:10px;background:white}.badge{font-size:12px;border-radius:5px;background:#eff3f8;padding:3px 9px}.counts{font-size:14px;margin:14px 0}.counts span{background:#f5f7fa;padding:5px 9px;border-radius:5px}.mappings{padding:12px;background:#f0f6fc;border-radius:7px;font-size:14px}.mappings p{margin:5px 0}details{margin-top:13px}summary{cursor:pointer;color:#315b86}pre{font-size:12px;line-height:1.6;white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f7fa;padding:14px;border-radius:7px}.buttons button{white-space:nowrap}button,input{font:inherit}button{padding:8px 12px;border:1px solid #b9c9dc;border-radius:6px;background:#f7faff;color:#254c78;cursor:pointer}button:disabled{opacity:.55;cursor:default}input{padding:8px;border:1px solid #b9c9dc;border-radius:6px}
</style>
