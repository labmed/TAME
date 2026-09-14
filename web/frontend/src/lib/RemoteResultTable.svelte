<script>
  import ResultTable from './ResultTable.svelte';
  import { getAnalysisTablePage, downloadAnalysisFile } from './api.js';
  export let name;
  export let info;
  let rows = null;
  let offset = 0;
  let busy = false;
  let error = '';
  async function load(next = 0) {
    busy = true; error = '';
    try {const result = await getAnalysisTablePage(info.pageUrl, next); rows = result.rows; offset = result.offset;}
    catch (e) {error = e.message;}
    finally {busy = false;}
  }
  async function download() {
    busy = true; error = '';
    try {await downloadAnalysisFile({url:info.downloadUrl, filename:`${name}.csv`});}
    catch(e) {error = e.message;}
    finally {busy = false;}
  }
</script>

<details class="result-details remote-result-table">
  <summary>{name} · {info.rowCount.toLocaleString()}행 · 필요할 때 조회</summary>
  <p>전체 감사 기록은 서버와 보고서 ZIP에 보존됩니다. CSV는 Excel 호환 UTF-8 BOM 형식입니다.</p>
  <button on:click={download} disabled={busy}>전체 CSV 저장</button>
  {#if rows === null}<button on:click={() => load()} disabled={busy}>50행 미리보기</button>{/if}
  {#if busy}<span role="status">불러오는 중…</span>{/if}
  {#if error}<p role="alert">{error}</p>{/if}
  {#if rows !== null}
    <ResultTable {name} {rows} />
    <div class="pagination"><button on:click={() => load(Math.max(0, offset-50))} disabled={busy || offset === 0}>이전 50행</button><span>{info.rowCount ? offset+1 : 0}–{offset+rows.length} / {info.rowCount.toLocaleString()}행</span><button on:click={() => load(offset+50)} disabled={busy || offset+50 >= info.rowCount}>다음 50행</button></div>
  {/if}
</details>
