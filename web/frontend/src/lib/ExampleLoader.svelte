<script>
  import { createEventDispatcher, onMount, onDestroy } from 'svelte';
  import {startNhanesDownload, getNhanesJob, getNhanesDataset, cancelNhanesJob, openExample} from './api.js';
  export let disabled = false;
  const dispatch = createEventDispatcher();
  let cycle = '2017-2018';
  let jobId = '';
  let loading = false;
  let status = '';
  let failure = '';
  let completed = 0;
  let total = 5;
  let timer;
  let destroyed = false;
  const storageKey = 'tametools.example.job';
  onDestroy(() => {destroyed = true; clearTimeout(timer);});
  onMount(() => {
    try {const prior = JSON.parse(localStorage.getItem(storageKey) || 'null');
      if (prior?.id) {jobId = prior.id; cycle = prior.cycle; loading = true; poll();}
    } catch { /* storage is optional */ }
  });
  function remember() {try {localStorage.setItem(storageKey, JSON.stringify({id:jobId, cycle}));} catch {}}
  function forget() {try {localStorage.removeItem(storageKey);} catch {}}
  async function loadN() {
    if (loading || disabled) return;
    loading = true; failure = ''; status = 'CDC 공개자료를 준비합니다.';
    try {const result = await startNhanesDownload(cycle); jobId = result.job_id; remember(); await poll();}
    catch (error) {failure = error.message; loading = false; dispatch('error', failure);}
  }
  async function poll() {
    if (destroyed) return;
    try {
      const job = await getNhanesJob(jobId);
      status = job.message; completed = job.completed; total = job.total;
      if (job.status === 'ready') {
        status = '작업실에 데이터를 불러옵니다.';
        const payload = await getNhanesDataset(jobId);
        if (destroyed) return;
        forget(); loading = false; dispatch('loaded', payload);
      } else if (job.status === 'error' || job.status === 'cancelled') {
        loading = false; forget(); failure = job.error || job.message; dispatch('error', failure);
      } else {timer = setTimeout(poll, 700);}
    } catch (error) {loading = false; forget(); failure = error.message; dispatch('error', failure);}
  }
  async function loadKenya() {
    if (loading || disabled) return;
    jobId = ''; loading = true; failure = ''; status = '공개 참고인 자료를 불러옵니다.';
    try {const payload = await openExample('kenya'); loading = false; dispatch('loaded', payload);}
    catch (error) {failure = error.message; loading = false; dispatch('error', failure);}
  }
  async function cancel() {
    try {await cancelNhanesJob(jobId); status = '작업 취소를 요청했습니다.';}
    catch (error) {failure = error.message;}
  }
</script>

<div class="example-loader">
  <p>공개자료를 공통 작업실에 불러와 태그·분석·저장을 시험합니다. 파일을 직접 열어도 같은 기능을 사용할 수 있습니다.</p>
  <div class="example-options">
    <div><strong>NHANES · 일반 인구 임상화학</strong><label for="example-cycle">조사 시기</label>
      <select id="example-cycle" bind:value={cycle} disabled={loading || disabled}><option value="2017-2018">2017–2018</option><option value="2021-2023">2021–2023</option></select>
      <button on:click={loadN} disabled={loading || disabled}>NHANES 불러오기</button>
      <small>첫 실행은 CDC에서 다운로드합니다. 검증된 캐시는 재사용합니다. 건강한 참고인 자료가 아닙니다.</small>
    </div>
    <div><strong>Kenya · 공개 참고인 자료</strong><p>533명 · AST, ALT, albumin, creatinine, GGT, total protein. 개인별 연령은 제공되지 않습니다.</p>
      <button on:click={loadKenya} disabled={loading || disabled}>Kenya 참고인 자료 불러오기</button>
      <small>배포본에 포함된 공개자료로 EP28 참고구간과 보고서를 시험할 수 있습니다.</small>
    </div>
  </div>
  {#if loading}<div class="task-progress" role="status"><span class="spinner"></span>{status}<progress max={total} value={completed}></progress>{#if jobId}<button on:click={cancel}>불러오기 취소</button>{/if}</div>{/if}
  {#if failure}<p class="error" role="alert">{failure}</p>{/if}
</div>
