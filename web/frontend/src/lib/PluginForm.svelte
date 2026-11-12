<script>
  // The questions a plugin declared with register_plugin(form=...), drawn as inputs.
  // The server resolves column questions into choices, so only these kinds arrive:
  // text, multiline, number, integer, choice, multi, check, date, file.
  export let form;
  export let answers = {};
  export let errors = {};
  export let disabled = false;

  const inputId = (item) => `plugin-field-${item.key}`;
  const described = (item) => [item.hint || item.kind === 'file' ? `${inputId(item)}-hint` : '', errors[item.key] ? `${inputId(item)}-error` : '']
    .filter(Boolean).join(' ') || undefined;

  function toggle(item, value, checked) {
    const picked = new Set(Array.isArray(answers[item.key]) ? answers[item.key] : []);
    if (checked) picked.add(value); else picked.delete(value);
    answers = {...answers, [item.key]: item.choices.map((choice) => choice.value).filter((choice) => picked.has(choice))};
  }
</script>

<fieldset class="plugin-form" {disabled}>
  <legend>{form.title || '플러그인 설정'}</legend>
  {#if form.intro}<p class="plugin-form-intro">{form.intro}</p>{/if}
  {#each form.fields as item (item.key)}
    <div class="field" class:invalid={Boolean(errors[item.key])} data-plugin-field={item.key}>
      {#if item.kind === 'check'}
        <label class="plugin-form-check">
          <input id={inputId(item)} type="checkbox" bind:checked={answers[item.key]} aria-describedby={described(item)} />
          <span>{item.label}</span>
        </label>
      {:else if item.kind === 'multi'}
        <span class="plugin-form-label" id={`${inputId(item)}-label`}>{item.label}{#if item.required}<span class="required-mark" title="필수 항목"> *</span>{/if}</span>
        <div class="plugin-form-choices" role="group" aria-labelledby={`${inputId(item)}-label`} aria-describedby={described(item)}>
          {#each item.choices as choice}
            <label class="plugin-form-check">
              <input type="checkbox" checked={(answers[item.key] || []).includes(choice.value)}
                on:change={(event) => toggle(item, choice.value, event.currentTarget.checked)} />
              <span>{choice.label}</span>
            </label>
          {/each}
        </div>
      {:else}
        <label for={inputId(item)}>{item.label}{#if item.required}<span class="required-mark" title="필수 항목"> *</span>{/if}</label>
        {#if item.kind === 'choice'}
          <select id={inputId(item)} bind:value={answers[item.key]} aria-required={item.required} aria-invalid={Boolean(errors[item.key])} aria-describedby={described(item)}>
            <option value="">{item.required ? '선택하세요' : '(지정 안 함)'}</option>
            {#each item.choices as choice}<option value={choice.value}>{choice.label}</option>{/each}
          </select>
        {:else if item.kind === 'multiline'}
          <textarea id={inputId(item)} rows="4" bind:value={answers[item.key]} aria-required={item.required} aria-invalid={Boolean(errors[item.key])} aria-describedby={described(item)}></textarea>
        {:else if item.kind === 'number' || item.kind === 'integer'}
          <input id={inputId(item)} type="number" bind:value={answers[item.key]} min={item.minimum ?? undefined} max={item.maximum ?? undefined}
            step={item.kind === 'integer' ? 1 : 'any'} aria-required={item.required} aria-invalid={Boolean(errors[item.key])} aria-describedby={described(item)} />
        {:else if item.kind === 'date'}
          <input id={inputId(item)} type="date" bind:value={answers[item.key]} aria-required={item.required} aria-invalid={Boolean(errors[item.key])} aria-describedby={described(item)} />
        {:else}
          <input id={inputId(item)} type="text" bind:value={answers[item.key]} spellcheck="false" aria-required={item.required} aria-invalid={Boolean(errors[item.key])} aria-describedby={described(item)} />
        {/if}
      {/if}
      {#if item.hint || item.kind === 'file'}
        <small class="plugin-form-hint" id={`${inputId(item)}-hint`}>{item.hint}{#if item.kind === 'file'}{item.hint ? ' · ' : ''}웹 작업실에서는 파일 찾기 창을 띄우지 않습니다. 이 프로그램이 실행 중인 PC(서버)에서 읽을 수 있는 경로를 글로 적어 주세요. 브라우저에서 고른 파일을 올려 주지는 않습니다.{/if}</small>
      {/if}
      {#if errors[item.key]}<small class="plugin-form-error" id={`${inputId(item)}-error`} role="alert">{errors[item.key]}</small>{/if}
    </div>
  {/each}
  {#if form.fields.some((item) => item.required)}<p class="plugin-form-legend"><span class="required-mark">*</span> 필수 항목 · 비워 둔 항목은 플러그인 기본값을 씁니다.</p>{/if}
</fieldset>

<style>
  .plugin-form { border: 1px solid #cfd8e3; border-radius: 6px; margin: 0 0 8px; padding: 10px 12px; max-width: 640px; background: #fbfcfe; }
  .plugin-form legend { padding: 0 6px; font-weight: 650; color: #26384b; }
  .plugin-form-intro { margin: 0 0 10px; color: #4e5d6d; }
  .plugin-form-label { color: #566574; font-size: 12px; }
  .plugin-form-check { display: flex; align-items: center; gap: 6px; color: #34495e; font-size: 13px; }
  .plugin-form-check input { width: auto; min-height: 0; }
  .plugin-form-choices { display: flex; flex-wrap: wrap; gap: 4px 18px; }
  .plugin-form-hint { color: #5f6d7b; }
  .plugin-form-error { color: #b42318; font-weight: 600; }
  .plugin-form-legend { margin: 6px 0 0; color: #5f6d7b; font-size: 12px; }
  .required-mark { color: #b42318; font-weight: 700; }
  .invalid :is(input:not([type=checkbox]), select, textarea) { border-color: #b42318; outline: 1px solid #b42318; }
</style>
