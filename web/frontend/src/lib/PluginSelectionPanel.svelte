<script>
  import { createEventDispatcher } from 'svelte';
  export let dataset;
  export let busy = false;
  const dispatch = createEventDispatcher();
  let mode = dataset?.pluginSelection?.mode || 'all';
  let selected = [...(dataset?.pluginSelection?.enabled || [])];
  let extraName = '';
  $: catalog = dataset?.pluginCatalog || dataset?.plugins || [];
  $: extras = selected.filter(name => !catalog.some(plugin => plugin.name === name));
  $: effective = resolve(selected, catalog);
  $: added = [...effective].filter(name => !selected.includes(name));
  $: dirty = mode !== (dataset?.pluginSelection?.mode || 'all') || (mode === 'selected' &&
    JSON.stringify([...selected].sort()) !== JSON.stringify([...(dataset?.pluginSelection?.enabled || [])].sort()));
  function resolve(names, plugins) {
    const found = new Set();
    function visit(name) {
      if (found.has(name)) return;
      found.add(name);
      const plugin = plugins.find(item => item.name === name);
      for (const child of [...(plugin?.dependencies || []), ...(plugin?.aliases || [])]) visit(child);
    }
    names.forEach(visit);
    return found;
  }
  function toggle(name, checked) {
    selected = checked ? [...new Set([...selected, name])] : selected.filter(item => item !== name);
  }
  function addName() {
    const name = extraName.trim().toUpperCase().replace(/-/g, '_');
    if (name) { toggle(name, true); extraName = ''; }
  }
</script>

<section class="plugin-selection">
  <h2>이 TAME 파일에서 사용할 플러그인</h2>
  <p>작업 입력: <strong>{dataset?.filename || '파일을 먼저 불러오세요'}</strong></p>
  <p>선택을 적용한 후 TAME로 저장하면 다음에 열 때도 같은 목록을 사용합니다. 연결에 필요한 하위 플러그인은 함께 사용할 수 있습니다.</p>
  <fieldset disabled={!dataset || busy}>
    <legend>사용 범위</legend>
    <label><input type="radio" bind:group={mode} value="selected" />선택한 플러그인만 사용</label>
    <label><input type="radio" bind:group={mode} value="all" />설치된 전체 플러그인 사용 · 기존 동작</label>
    {#if mode === 'selected'}
      <p>직접 선택: {selected.join(', ') || '없음'}</p>
      <details open={!(dataset?.pluginSelection?.enabled?.length)}><summary>플러그인 목록 편집</summary>
      <div class="selection-tools"><button type="button" on:click={() => selected = catalog.map(p => p.name)}>목록 전체 선택</button><button type="button" on:click={() => selected = []}>선택 해제</button></div>
      <div class="plugin-choices">
        {#each catalog as plugin}
          <label class="plugin-choice">
            <input type="checkbox" checked={selected.includes(plugin.name)} on:change={event => toggle(plugin.name, event.currentTarget.checked)} />
            <span><strong>{plugin.name}</strong>{#if plugin.requiresTrust} · 외부 코드 신뢰 확인 필요{/if}{#if !plugin.available} · 이 환경에서 미확인{/if}
              <small>{plugin.description}</small>
              {#if plugin.dependencies?.length}<small>연결: {plugin.dependencies.join(', ')}</small>{/if}
            </span>
          </label>
        {/each}
        {#each extras as name}<label><input type="checkbox" checked on:change={() => toggle(name, false)} />{name} · 설치 여부는 실행 시 확인</label>{/each}
      </div>
      <div class="add-plugin"><label for="additional-plugin">외부 플러그인 이름</label><input id="additional-plugin" bind:value={extraName} placeholder="MY_PLUGIN" /><button type="button" on:click={addName} disabled={!extraName.trim()}>목록에 추가</button></div>
      </details>
      {#if added.length}<p>연결·별칭으로 함께 사용: {added.join(', ')}</p>{/if}
      {#if !selected.length}<p>플러그인을 사용하지 않습니다. 기본 전처리·탐색 분석은 계속 사용할 수 있습니다.</p>{/if}
    {/if}
    <button class="primary" type="button" disabled={!dirty} on:click={() => dispatch('apply', mode === 'all' ? null : selected)}>선택 적용</button>
    {#if dirty}<span role="status"> 아직 적용하지 않은 선택입니다.</span>{/if}
  </fieldset>
  {#each dataset?.pluginSelection?.warnings || [] as warning}<p role="status">{warning}</p>{/each}
</section>

<style>
  .plugin-selection { margin-bottom: 1.5rem; }
  fieldset { border: 1px solid #cbd5e1; border-radius: 8px; padding: 1rem; }
  label { display: flex; align-items: start; gap: .5rem; margin: .5rem 0; }
  input[type=checkbox], input[type=radio] { width: auto; margin-top: .25rem; }
  .plugin-choices { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: .5rem; margin: 1rem 0; }
  .plugin-choice { background: #f8fafc; padding: .6rem; border: 1px solid #e2e8f0; border-radius: 6px; margin: 0; }
  small { display: block; color: #475569; margin-top: .3rem; }
  .selection-tools, .add-plugin { display: flex; flex-wrap: wrap; gap: .5rem; align-items: center; margin: .75rem 0; }
</style>
