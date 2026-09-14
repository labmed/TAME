<script>
  import { displayValue, displayUnit, groupLabel } from './display.js';
  export let rows = [];
  export let name = '';
  export let labels = {};
  let details = false;
  let page = 0;
  const surveyColumns = ['result_id','group','analysis_n','mean','ci95_low','ci95_high','unit','policy','status'];
  const surveyLabels = {result_id:'검사',group:'집단',analysis_n:'유효 n',mean:'가중 평균',ci95_low:'95% CI 하한',ci95_high:'95% CI 상한',unit:'단위',policy:'검열값 정책',status:'상태'};
  function value(row, column) {
    if (column === 'unit') return displayUnit(row[column]);
    if (column === 'group') return groupLabel(row[column]);
    if (column === 'result_id') return labels?.[row[column]] || row[column];
    return displayValue(row[column]);
  }
  const size = 50;
  $: pages = Math.max(1, Math.ceil(rows.length / size));
  $: if (page >= pages) page = 0;
  $: columns = rows.length ? name === 'survey_means' && !details ? surveyColumns.filter(c => c in rows[0]) : Object.keys(rows[0]) : [];
</script>

<div class="table-block">
  <div class="block-header"><h3>{name}</h3><span>{rows.length.toLocaleString()}행</span></div>
  {#if name === 'survey_means'}<label class="check-row"><input type="checkbox" bind:checked={details} />설계·가중치·엔진 상세 열 표시</label><p>가중 평균과 95% 신뢰구간입니다. 집단은 서로 겹칠 수 있습니다.</p>{/if}
  {#if rows.length}
    <div class="table-wrap"><table><thead><tr>{#each columns as column}<th>{name === 'survey_means' ? surveyLabels[column] || column : column}</th>{/each}</tr></thead>
    <tbody>{#each rows.slice(page * size, (page + 1) * size) as row}<tr>{#each columns as column}<td title={String(row[column] ?? '')}>{value(row,column)}</td>{/each}</tr>{/each}</tbody></table></div>
    {#if pages > 1}<div class="pagination"><button on:click={() => page -= 1} disabled={page === 0}>이전</button><span>{page + 1} / {pages}</span><button on:click={() => page += 1} disabled={page + 1 === pages}>다음</button><small>화면은 {size}행씩 표시합니다. 저장 파일에는 전체 결과가 포함됩니다.</small></div>{/if}
  {:else}<p>해당하는 결과가 없습니다.</p>{/if}
</div>
