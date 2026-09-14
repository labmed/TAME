<script>
  import { tick, onDestroy } from 'svelte';
  import ExampleLoader from '$lib/ExampleLoader.svelte';
  import ReferencePanel from '$lib/ReferencePanel.svelte';
  import SexNormalizationPanel from '$lib/SexNormalizationPanel.svelte';
  import RemoteResultTable from '$lib/RemoteResultTable.svelte';
  import ResultTable from '$lib/ResultTable.svelte';
  import LogPanel from '$lib/LogPanel.svelte';
  import { displayValue, interval, statusLabels, referenceNote, groupLabel, methodLabels, displayUnit, commonReferenceNotes, testLabel, settingsSummary } from '$lib/display.js';
  import 'ag-grid-community/styles/ag-grid.css';
  import 'ag-grid-community/styles/ag-theme-quartz.css';
  import {
    applyDatasetAction,
    applyDatasetActionPipeline,
    applyDatasetFix,
    applyDatasetPreset,
    applyDatasetTagPlacement,
    anonymizeDataset,
    convertWorkbook,
    createDatasetPlugin,
    downloadDataset,
    openFile,
    refreshDataset,
    reviewDataset,
    runDatasetPlugin,
    runEda,
    runMetaAnalysis,
    runReferenceInterval,
    runPlannedAnalysis,
    downloadAnalysisFile,
    saveBlob
  } from '$lib/api.js';

  let fileInput;
  let gridHost;
  let createGrid;
  let gridApi;

  let dataset = null;
  let inputDatasetId = '';
  let gridDatasetId = '';
  let showExamples = false;
  let showProperties = false;
  let busyLabel = '작업을 실행하고 있습니다.';
  onDestroy(() => gridApi?.destroy());
  async function openExampleDataset(event) {
    await runTask(async () => {
      await setDataset(event.detail, {action:'공개 예제 불러오기'});
      showExamples = false;
      message = `${event.detail.filename} · ${event.detail.rowCount.toLocaleString()}행을 불러왔습니다.`;
    }, '예제 자료를 작업실에 불러옵니다.');
  }
  let workspaceItems = [];
  let activeDatasetId = '';
  let nextDatasetId = 1;
  let workbook = null;
  let selectedSheets = [];
  let conversionMode = 'single';
  let activeTab = 'data';
  let message = '파일 또는 공개 예제를 불러와 시작하세요.';
  let error = '';
  let failureAuditFile = null;
  let busy = false;
  let metaText = '';
  let validationReview = null;
  let edaTables = {};
  let edaWarnings = [];

  let selectedField = '';
  let columnNameDraft = '';
  let tagsDraft = '';
  let columnLabelDraft = '';
  let columnDescriptionDraft = '';
  let columnUnitDraft = '';
  let columnUcumUnitDraft = '';
  let columnLoincDraft = '';
  let columnLocalCodeDraft = '';
  let columnPhiDraft = false;
  let pivotTestNameDraft = '';
  let pivotUnitDraft = '';
  let pivotRefLowDraft = '';
  let pivotRefHighDraft = '';
  let tagDefinitionName = 'AGE5';
  let tagDefinitionLabel = '5-year age';
  let tagDefinitionDescription = 'AGE-compatible custom tag using 5-year grouping.';
  let tagDefinitionInherits = 'AGE';
  let tagDefinitionAgeBinWidth = '5';
  let hashTags = 'ID,ID(patient),ID(hospital),PATIENT_ID,HOSPITAL_ID';
  let dropTags = 'NAME';
  let salt = '';
  let ageBinWidth = '10';
  let numComparatorHandling = '';
  let pluginName = 'ROW_SAMPLE';
  let pluginDescription = 'Return the first rows of the current dataset.';
  let pluginSource = `def run(dataset, options):
    rows = int(options.get("rows", 20))
    return dataset.df.head(rows).copy()
`;
  let selectedPlugin = '';
  let pluginOptionsText = '{}';

  const defaultCommonTags = [
    'RESULT',
    'NUM',
    '<NUM>',
    'AGE',
    'AGE5',
    'SEX',
    'CATEGORY',
    'DATETIME',
    'TESTNAME',
    'ITEM',
    'ID',
    'ID(patient)',
    'ID(sample)',
    'ID(hospital)',
    'HOSPITAL_ID',
    'NAME',
    'BY',
    'REF_LOW',
    'REF_HIGH',
    'UNIT',
    'STR'
  ];
  $: tagCatalog = dataset?.tagCatalog || [];
  $: tagDefinitions = dataset?.tagDefinitions || [];
  $: roleSummary = dataset?.roleSummary || {};
  $: resultColumns = roleSummary.resultColumns || [];
  $: pivotContextColumns = roleSummary.pivotContextColumns || [];
  $: qualifiedIds = roleSummary.qualifiedIds || [];
  $: ageColumns = roleSummary.ageColumns || [];
  $: tagPalette = dataset?.commonTags?.length ? dataset.commonTags : defaultCommonTags;
  const commandItems = [
    {label:'태그를 META로 이동', run:() => applyTagPlacement('meta'), mutates:true},
    {label:'태그를 헤더로 이동', run:() => applyTagPlacement('header'), mutates:true},
  ];
  const wizardItems = [
    { title: '1. 데이터 상태 확인', detail: '열·행·태그와 값 분포를 확인합니다.', run: executeReview },
    { title: '2. 검증·표준화 확인', detail: 'SEX, AGE, RESULT 등 태그 규칙 위반을 찾습니다.', run: executeValidate },
    { title: '3. 성별값 표준화', detail: '출처별 코드표를 지정하고 원 값을 보존합니다.', run: openSexSettings },
    { title: '4. 나이 표기 표준화', detail: '10a, 2m, 1day 등을 표준 AGE 형식으로 바꿉니다.', run: () => applyValidationFix('standardize-age') },
    { title: '5. 부등호 처리 정책', detail: '원문과 행을 보존하고 계산 정책을 선택합니다.', run: openComparatorSettings },
    { title: '6. 탐색 분석 실행', detail: '요약통계, 분포, 부등호값 현황을 만듭니다.', run: executeEda },
    { title: '7. EP28 참고구간', detail: '방법·이상치·분할 설정 후 후보 구간을 계산합니다.', run: openReferenceSettings },
    { title: '8. 개인정보 비식별화', detail: 'ID/HOSPITAL_ID는 해시 처리하고 NAME은 제거합니다.', run: executeAnonymize },
    { title: '9. 대표 전처리 묶음 실행', detail: '파일 안의 ACTION_PIPELINE을 한 번에 실행합니다.', run: executeFirstActionPipeline },
    { title: '10. 저장된 분석 실행', detail: '저장된 분석 계획 또는 대표 분석을 실행합니다.', run: executeFirstMetaAnalysis }
  ];

  $: activeItem = workspaceItems.find((item) => item.id === activeDatasetId) || null;
  $: inputItem = workspaceItems.find(item => item.id === inputDatasetId && item.kind === 'input') || null;
  $: inputDataset = inputItem?.dataset || null;
  $: referenceRows = dataset?.analysisTables?.reference_intervals || [];
  $: commonRiNotes = commonReferenceNotes(referenceRows);
  $: visibleWarnings = [...new Set(edaWarnings.map(w => String(w || '').trim()).filter(Boolean))];
  $: selectedColumn = dataset?.columns?.find((column) => column.field === selectedField) || null;
  $: sexColumnCount = dataset?.columns?.filter((column) => column.tags?.includes('SEX')).length || 0;
  $: metaActions = inputDataset?.actions || [];
  $: metaActionPipelines = inputDataset?.actionPipelines || [];
  $: metaAnalyses = inputDataset?.analyses || [];
  $: presetList = inputDataset?.presets || [];
  $: pluginList = inputDataset?.plugins || [];
  $: selectedPluginSpec = pluginList.find((plugin) => plugin.name === selectedPlugin) || null;
  $: if (pluginList.length && !pluginList.some((plugin) => plugin.name === selectedPlugin)) {
    selectedPlugin = pluginList.some(p => p.name === 'RI_EP28') ? 'RI_EP28' : pluginList[0].name;
  }
  $: validationIssues = validationReview?.issues || dataset?.issues || [];
  $: validationProfiles = validationReview?.profiles || [];
  $: rowStatus = dataset
    ? `${dataset.rowCount.toLocaleString()} 행 · ${dataset.columns.length.toLocaleString()}열`
    : '자료를 불러오세요';

  async function setDataset(payload, options = {}) {
    await addWorkspaceDataset(payload, options);
  }

  async function addWorkspaceDataset(payload, options = {}) {
    persistActiveDataset();
    const id = `ds-${nextDatasetId++}`;
    const item = {
      id,
      parentId: options.parentId || '',
      kind: options.kind || payload.dataRole || 'input',
      action: options.action || 'open',
      title: payload.filename || `dataset-${nextDatasetId - 1}.tame`,
      createdAt: new Date().toLocaleTimeString(),
      dataset: payload,
      edaTables: options.edaTables || payload.analysisTables || {},
      edaWarnings: options.edaWarnings || payload.warnings || []
    };
    workspaceItems = [...workspaceItems, item];
    if (item.kind === 'input') inputDatasetId = id;
    await activateWorkspaceItem(id, { skipPersist: true, tab: options.tab || 'data' });
    message = options.message || `${payload.filename || 'dataset'} loaded`;
  }

  async function replaceActiveDataset(payload, options = {}) {
    if (!activeDatasetId) return;
    dataset = payload;
    metaText = payload.metaText || '';
    validationReview = null;
    edaTables = options.edaTables || edaTables;
    edaWarnings = options.edaWarnings || edaWarnings;
    if (!payload.columns?.some((column) => column.field === selectedField)) {
      selectedField = payload.columns?.[0]?.field || '';
    }
    workspaceItems = workspaceItems.map((item) =>
      item.id === activeDatasetId
        ? {
            ...item,
            title: payload.filename || item.title,
            dataset: payload,
            edaTables,
            edaWarnings
          }
        : item
    );
    syncColumnDrafts();
    await tick();
    if (activeTab === 'data') {
      await mountGrid();
    }
    message = options.message || `${payload.filename || 'dataset'} updated`;
  }

  async function activateWorkspaceItem(id, options = {}) {
    if (!options.skipPersist) {
      persistActiveDataset();
    }
    const item = workspaceItems.find((entry) => entry.id === id);
    if (!item) return;
    activeDatasetId = id;
    if (item.kind === 'input') inputDatasetId = id;
    dataset = item.dataset;
    metaText = dataset.metaText || '';
    workbook = null;
    selectedField = dataset.columns?.[0]?.field || '';
    syncColumnDrafts();
    activeTab = options.tab || 'data';
    validationReview = null;
    edaTables = item.edaTables || {};
    edaWarnings = item.edaWarnings || [];
    message = `${item.title}를 보고 있습니다.`;
    await tick();
    await mountGrid();
  }

  async function deleteWorkspaceItem(id) {
    const index = workspaceItems.findIndex((item) => item.id === id);
    if (index < 0) return;
    const wasActive = id === activeDatasetId;
    const remaining = workspaceItems.filter((item) => item.id !== id);
    workspaceItems = remaining;
    if (inputDatasetId === id) inputDatasetId = remaining.filter(item => item.kind === 'input').at(-1)?.id || '';

    if (!wasActive) {
      message = '작업 이력에서 자료를 제거했습니다.';
      return;
    }

    if (gridApi) {
      gridApi.destroy();
      gridApi = null;
    }

    if (!remaining.length) {
      activeDatasetId = '';
      dataset = null;
      metaText = '';
      workbook = null;
      selectedField = '';
      validationReview = null;
      edaTables = {};
      edaWarnings = [];
      activeTab = 'data';
      message = '작업 이력이 비어 있습니다.';
      return;
    }

    const nextIndex = Math.min(index, remaining.length - 1);
    await activateWorkspaceItem(remaining[nextIndex].id, { skipPersist: true, tab: activeTab });
    message = '작업 이력에서 자료를 제거했습니다.';
  }

  function persistActiveDataset(extra = {}) {
    if (!activeDatasetId || !dataset) return;
    const nextDataset = { ...dataset, metaText };
    dataset = nextDataset;
    workspaceItems = workspaceItems.map((item) =>
      item.id === activeDatasetId
        ? {
            ...item,
            ...extra,
            title: nextDataset.filename || item.title,
            dataset: nextDataset,
            edaTables,
            edaWarnings
          }
        : item
    );
  }

  async function mountGrid() {
    if (!gridHost || !dataset) return;
    if (!createGrid) createGrid = (await import('ag-grid-community')).createGrid;
    const options = {rowData:dataset.rows, columnDefs:buildColumnDefs()};
    if (gridApi) {
      gridApi.updateGridOptions(options);
    } else {
      gridApi = createGrid(gridHost, {
        ...options,
        defaultColDef:{editable:() => activeItem?.kind !== 'result',filter:true,sortable:true,resizable:true,minWidth:135,width:165},
        suppressMenuHide:true, enableBrowserTooltips:true, tooltipShowDelay:250,
        animateRows:false, rowSelection:'single',
        onCellValueChanged:syncRowsFromGrid,
        onColumnHeaderClicked:event => selectColumn(event.column.getColDef().field),
      });
    }
    gridDatasetId = activeDatasetId;
  }

  function buildColumnDefs() {
    if (!dataset) return [];
    return [
      {
        field: '__rowid',
        headerName: '#',
        headerTooltip: 'Row number',
        editable: false,
        pinned: 'left',
        width: 72,
        filter: false
      },
      ...dataset.columns.map((column) => ({
        field: column.field,
        headerName: dataHeader(column),
        headerTooltip: taggedHeader(column),
        valueFormatter: params => displayValue(params.value, column.tags.some(tag => ['NUM','<NUM>','SURVEY_WEIGHT'].includes(tag)) && !column.tags.some(tag => tag === 'STR' || tag === 'ID' || tag.startsWith('ID('))),
        tooltipValueGetter: (params) => params.value,
        cellDataType: false
      }))
    ];
  }

  function dataHeader(column) {
    return column.metadata?.LABEL || column.name;
  }

  function taggedHeader(column) {
    return column.tags?.length ? `[[${column.tags.join('::')}]]${column.name}` : column.name;
  }

  function syncRowsFromGrid() {
    if (!gridApi || !dataset || gridDatasetId !== activeDatasetId) return;
    const rows = [];
    gridApi.forEachNode((node) => rows.push({ ...node.data }));
    const dataRows = Array.isArray(dataset.dataRows)
      ? dataset.dataRows.map((row) => (Array.isArray(row) ? [...row] : rowToArray(row)))
      : rows.map((row) => rowToArray(row));
    rows.forEach((row, previewIndex) => {
      const rowIndex = Number(row.__rowid) > 0 ? Number(row.__rowid) - 1 : previewIndex;
      if (rowIndex >= 0) {
        dataRows[rowIndex] = rowToArray(row);
      }
    });
    dataset = { ...dataset, rows, dataRows };
    persistActiveDataset();
  }

  function rowToArray(row) {
    return (dataset?.columns || []).map((column, index) => row?.[column.field] ?? row?.[`c${index}`] ?? null);
  }

  async function showTab(tab) {
    if (activeTab === 'data') syncRowsFromGrid();
    persistActiveDataset();
    activeTab = tab;
    await tick();
    if (tab === 'data') await mountGrid();
  }

  function currentDataset() {
    if (!dataset) return null;
    syncRowsFromGrid();
    persistActiveDataset();
    return {
      ...dataset,
      metaText
    };
  }

  function inputSnapshot() {
    if (!inputDatasetId) {error = '작업 입력 자료가 없습니다. 원자료 또는 전처리 자료를 선택해 주세요.'; return null;}
    if (activeDatasetId === inputDatasetId) return currentDataset();
    persistActiveDataset();
    return workspaceItems.find(item => item.id === inputDatasetId)?.dataset || null;
  }
  function roleProblem(plugin) {
    const source = inputDataset;
    if (!source) return '입력 자료를 먼저 불러오세요.';
    for (const role of plugin?.roles || []) {
      if (!['one','one_or_more'].includes(role.cardinality)) continue;
      const columns = role.name === 'result' ? source.roleSummary?.resultColumns || [] :
        source.columns.filter(c => c.tags.some(tag => role.tags.some(t => tag === t || tag.startsWith(t+'('))));
      if (!columns.length) return `필요한 역할: ${role.name} (${role.tags.join(', ')})`;
    }
    return '';
  }
  async function openReferenceSettings() {
    const payload = inputSnapshot(); if (!payload) return;
    const sourceId = inputDatasetId;
    await runTask(async () => {
      const refreshed = await refreshDataset(payload);
      if (activeDatasetId === sourceId) await replaceActiveDataset(refreshed);
      else workspaceItems = workspaceItems.map(item => item.id === sourceId ? {...item,dataset:refreshed} : item);
      await showTab('ri-settings');
      message = '입력 자료와 META에서 참고구간 설정을 불러왔습니다.';
    }, '입력 자료와 참고구간 설정을 확인합니다.');
  }
  async function openComparatorSettings() {
    if (!inputSnapshot()) return;
    await showTab('num-settings');
  }
  async function executePlannedAnalysis() {
    const payload = inputSnapshot(); if (!payload) return;
    const parentId = inputDatasetId;
    await runTask(async () => {
      const result = await runPlannedAnalysis(payload);
      await setDataset(result,{parentId,kind:'result',action:'저장된 분석',tab:'eda',message:'META의 대상자·정책·표본설계로 분석했습니다.'});
    }, '저장된 분석 조건으로 계산합니다.');
  }
  async function saveReport(file) {
    await runTask(async () => {await downloadAnalysisFile(file);message = `${file.label}를 저장했습니다.`;});
  }

  async function handleFileChange(event) {
    const file = event.target.files?.[0];
    if (!file) return;
    await loadFile(file);
    event.target.value = '';
  }

  async function loadFile(file) {
    await runTask(async () => {
      const payload = await openFile(file);
      if (payload.kind === 'xlsx') {
        workbook = payload;
        selectedSheets = defaultSheets(payload.sheets);
        conversionMode = selectedSheets.length > 1 ? 'merge' : 'single';
        message = `${payload.filename} workbook reviewed`;
        return;
      }
      await setDataset(payload, { action: 'opened', message: `${payload.filename || 'dataset'} loaded` });
    });
  }

  function defaultSheets(sheets) {
    const dataSheets = sheets.filter((sheet) => !sheet.isControl);
    const source = dataSheets.length ? dataSheets : sheets;
    return source.slice(0, 1).map((sheet) => sheet.name);
  }

  function toggleSheet(name) {
    if (conversionMode === 'single') {
      selectedSheets = [name];
      return;
    }
    selectedSheets = selectedSheets.includes(name)
      ? selectedSheets.filter((sheet) => sheet !== name)
      : [...selectedSheets, name];
  }

  function selectAllDataSheets() {
    if (!workbook) return;
    selectedSheets = workbook.sheets.filter((sheet) => !sheet.isControl).map((sheet) => sheet.name);
    conversionMode = 'merge';
  }

  async function convertSelectedSheets() {
    if (!workbook) return;
    await runTask(async () => {
      const payload = await convertWorkbook(workbook.workbookId, conversionMode, selectedSheets);
      await setDataset(payload, { action: 'xlsx conversion', message: `${payload.filename || 'dataset'} converted` });
    });
  }

  function selectColumn(field) {
    if (field === '__rowid') return;
    showProperties = true;
    selectedField = field;
    syncColumnDrafts();
  }

  function syncColumnDrafts() {
    const column = dataset?.columns?.find((item) => item.field === selectedField);
    columnNameDraft = column?.name || '';
    tagsDraft = column?.tags?.join(', ') || '';
    const metadata = column?.metadata || {};
    const context = metadata.PIVOT_CONTEXT || metadata.pivotContext || {};
    columnLabelDraft = metadata.LABEL || '';
    columnDescriptionDraft = metadata.DESCRIPTION || '';
    columnUnitDraft = metadata.UNIT || '';
    columnUcumUnitDraft = metadata.UCUM_UNIT || '';
    columnLoincDraft = metadata.LOINC || '';
    columnLocalCodeDraft = metadata.LOCAL_CODE || '';
    columnPhiDraft = Boolean(metadata.PHI);
    pivotTestNameDraft = context.TESTNAME || '';
    pivotUnitDraft = context.UNIT || metadata.UCUM_UNIT || metadata.UNIT || '';
    pivotRefLowDraft = context.REF_LOW || '';
    pivotRefHighDraft = context.REF_HIGH || '';
  }

  async function applyColumnMeta() {
    if (!dataset || !selectedField) return;
    await runTask(async () => {
      syncRowsFromGrid();
      const tags = splitTags(tagsDraft);
      const metadata = columnMetadataFromDrafts(tags);
      dataset = {
        ...dataset,
        columns: dataset.columns.map((column) =>
          column.field === selectedField
            ? { ...column, name: columnNameDraft.trim() || column.name, tags, taggedHeader: '', metadata }
            : column
        ),
        commonTags: mergeUnique([...(dataset.commonTags || []), ...tags])
      };
      persistActiveDataset();
      const refreshed = await refreshDataset(currentDataset());
      await replaceActiveDataset(refreshed, { message: 'Column tags and META updated' });
    });
  }

  function addTag(tag) {
    const existing = splitTags(tagsDraft);
    if (!existing.includes(tag)) {
      tagsDraft = [...existing, tag].join(', ');
    }
  }

  function splitTags(value) {
    return String(value || '')
      .split(',')
      .map((tag) => tag.trim())
      .filter(Boolean);
  }

  function mergeUnique(values) {
    return [...new Set(values.filter(Boolean))];
  }

  function columnMetadataFromDrafts(tags) {
    const metadata = { ...(selectedColumn?.metadata || {}) };
    delete metadata.effectiveTags;
    setDraftValue(metadata, 'LABEL', columnLabelDraft);
    setDraftValue(metadata, 'DESCRIPTION', columnDescriptionDraft);
    setDraftValue(metadata, 'UNIT', columnUnitDraft);
    setDraftValue(metadata, 'UCUM_UNIT', columnUcumUnitDraft);
    setDraftValue(metadata, 'LOINC', columnLoincDraft);
    setDraftValue(metadata, 'LOCAL_CODE', columnLocalCodeDraft);
    if (columnPhiDraft) {
      metadata.PHI = true;
    } else {
      delete metadata.PHI;
    }
    const pivotContext = {
      ...(metadata.PIVOT_CONTEXT || {}),
      TESTNAME: pivotTestNameDraft.trim(),
      UNIT: pivotUnitDraft.trim(),
      REF_LOW: pivotRefLowDraft.trim(),
      REF_HIGH: pivotRefHighDraft.trim()
    };
    const hasPivotContext = Object.values(pivotContext).some((value) => String(value || '').trim());
    if (hasPivotContext) {
      metadata.PIVOT_CONTEXT = cleanObject(pivotContext);
      metadata.REFERENCE_SOURCE = metadata.REFERENCE_SOURCE || 'PIVOT_CONTEXT';
    } else {
      delete metadata.PIVOT_CONTEXT;
      if (metadata.REFERENCE_SOURCE === 'PIVOT_CONTEXT') {
        delete metadata.REFERENCE_SOURCE;
      }
    }
    metadata.effectiveTags = tags;
    return cleanObject(metadata);
  }

  function setDraftValue(target, key, value) {
    const text = String(value || '').trim();
    if (text) {
      target[key] = text;
    } else {
      delete target[key];
    }
  }

  function cleanObject(object) {
    return Object.fromEntries(
      Object.entries(object || {}).filter(([, value]) => {
        if (value === null || value === undefined || value === '') return false;
        if (Array.isArray(value) && !value.length) return false;
        if (typeof value === 'object' && !Array.isArray(value) && !Object.keys(value).length) return false;
        return true;
      })
    );
  }

  function selectedColumnHasResult() {
    return splitTags(tagsDraft).some((tag) => tag === 'RESULT' || tag.startsWith('RESULT'));
  }

  async function applyTagDefinition() {
    if (!dataset) return;
    const name = tagDefinitionName.trim().toUpperCase();
    if (!name) {
      error = '새 태그 이름을 입력하세요.';
      return;
    }
    await runTask(async () => {
      const definition = cleanObject({
        name,
        label: tagDefinitionLabel.trim() || name,
        description: tagDefinitionDescription.trim(),
        inherits: splitTags(tagDefinitionInherits),
        ageBinWidth: tagDefinitionAgeBinWidth.trim()
      });
      const definitions = [
        ...(dataset.tagDefinitions || []).filter((item) => String(item.name).toUpperCase() !== name),
        definition
      ];
      dataset = {
        ...dataset,
        tagDefinitions: definitions,
        commonTags: mergeUnique([...(dataset.commonTags || []), name, ...splitTags(tagDefinitionInherits)])
      };
      persistActiveDataset();
      addTag(name);
      const refreshed = await refreshDataset(currentDataset());
      await replaceActiveDataset(refreshed, { message: `TAG_DEFINITIONS.${name} updated` });
    });
  }

  function selectedPluginRoleText(plugin) {
    if (plugin?.requiresTrust) return 'External Python plugin. Trust confirmation is required before execution.';
    if (!plugin?.roles?.length) return 'No declared role contract';
    return plugin.roles
      .map((role) => `${role.name}: ${role.tags.join('/')} (${role.cardinality}, ${role.iteration})`)
      .join('; ');
  }

  function confirmPluginTrust(target) {
    if (!target?.requiresTrust) return false;
    const name = target.name || target.label || target.plugin || 'external plugin';
    return window.confirm(
      `${name} 플러그인은 TAME 파일에 포함된 Python 코드를 실행합니다.\n신뢰할 수 있는 파일일 때만 실행하세요.`
    )
      ? true
      : null;
  }

  function leafTagNames(node) {
    if (!node?.children?.length) return node?.name ? [node.name] : [];
    return node.children.flatMap((child) => leafTagNames(child));
  }

  async function download(format) {
    const payload = currentDataset();
    if (!payload) return;
    await runTask(async () => {
      const blob = await downloadDataset(payload, format);
      saveBlob(blob, downloadFilename(payload, format));
      message = `${format.toUpperCase()} 파일을 저장했습니다.`;
    });
  }

  function downloadFilename(payload, format) {
    const fallback = format === 'xlsx' ? 'dataset.xlsx' : 'dataset.tame';
    const source = payload?.filename || fallback;
    if (format !== 'xlsx') return source.endsWith('.tame') ? source : `${source}.tame`;
    return source.replace(/(\.data|\.meta)?\.tame$/i, '').replace(/\.[^.]+$/i, '') + '.xlsx';
  }

  async function executeEda() {
    const payload = inputSnapshot();
    if (!payload) return;
    const parentId = inputDatasetId;
    await runTask(async () => {
      const result = await runEda(payload);
      const tables = result.tables || {};
      const warnings = result.warnings || [];
      if (result.dataset) {
        await setDataset(result.dataset, {
          parentId,
          action: '탐색 분석',
          kind:'result',
          edaTables: tables,
          edaWarnings: warnings,
          tab: 'eda',
          message: '탐색 분석을 완료했습니다. 다음 분석의 입력 자료는 유지됩니다.'
        });
      } else {
        edaTables = tables;
        edaWarnings = warnings;
        await showTab('eda');
        message = 'EDA complete';
      }
    });
  }

  async function executeReview() {
    if (!inputSnapshot()) return;
    if (activeDatasetId !== inputDatasetId) await activateWorkspaceItem(inputDatasetId);
    const payload = currentDataset();
    if (!payload) return;
    await runTask(async () => {
      const result = await reviewDataset(payload);
      validationReview = result;
      dataset = { ...dataset, issues: result.issues || [] };
      persistActiveDataset();
      await showTab('validate');
      message = '데이터 상태 확인을 완료했습니다.';
    });
  }

  async function executeReferenceInterval(options) {
    const payload = inputSnapshot(); if (!payload) return;
    const parentId = inputDatasetId;
    await runTask(async () => {
      const result = await runReferenceInterval(payload, options);
      await setDataset(result, {parentId,kind:'result',action:'EP28 참고구간',tab:'eda',
        message:`EP28 후보 구간 ${result.rowCount.toLocaleString()}행과 보고서를 생성했습니다.`});
    }, 'EP28 참고구간·신뢰구간과 보고서를 계산합니다.');
  }

  async function createPlugin() {
    const payload = currentDataset();
    if (!payload) return;
    const parentId = activeDatasetId;
    await runTask(async () => {
      const result = await createDatasetPlugin(payload, {
        name: pluginName,
        description: pluginDescription,
        source: pluginSource
      });
      await setDataset(result, {
        parentId,
        action: `plugin created: ${pluginName}`,
        tab: 'meta',
        message: 'Plugin created'
      });
      selectedPlugin = pluginName.trim().toUpperCase().replace(/-/g, '_');
    });
  }

  async function executePlugin() {
    const payload = inputSnapshot();
    if (!payload || !selectedPlugin) return;
    const problem = roleProblem(selectedPluginSpec);
    if (problem) {error = problem;return;}
    const allowPlugins = confirmPluginTrust(selectedPluginSpec);
    if (allowPlugins === null) return;
    const parentId = inputDatasetId;
    await runTask(async () => {
      const result = await runDatasetPlugin(payload, {
        plugin: selectedPlugin,
        pluginOptions: parsePluginOptions(),
        allowPlugins
      });
      await setDataset(result, {
        parentId,
        action: `plugin: ${selectedPlugin}`,
        kind: result.dataRole === 'result' || !result.roleSummary?.resultColumns?.length ? 'result' : 'input',
        tab: result.analysisTables || result.charts?.length ? 'eda' : 'data',
        message: 'Plugin output TAME generated'
      });
    });
  }

  function parsePluginOptions() {
    const text = pluginOptionsText.trim();
    if (!text) return {};
    return JSON.parse(text);
  }

  async function executeValidate() {
    if (!inputSnapshot()) return;
    if (activeDatasetId !== inputDatasetId) await activateWorkspaceItem(inputDatasetId);
    const payload = currentDataset();
    if (!payload) return;
    await runTask(async () => {
      const result = await reviewDataset(payload);
      validationReview = { issues: result.issues || [], profiles: [] };
      dataset = { ...dataset, issues: result.issues || [] };
      persistActiveDataset();
      await showTab('validate');
      message = `검증 항목 ${(result.issues || []).length}건을 확인했습니다.`;
    });
  }

  async function openSexSettings() {
    const payload = inputSnapshot();
    if (!payload) return;
    await runTask(async () => {
      const refreshed = await refreshDataset(payload);
      if (activeDatasetId === inputDatasetId) await replaceActiveDataset(refreshed);
      else workspaceItems = workspaceItems.map(item => item.id === inputDatasetId ? {...item, dataset:refreshed} : item);
      await showTab('sex-settings');
    }, '성별 코드 설정을 준비합니다.');
  }

  async function applyValidationFix(action, extra = {}) {
    const payload = inputSnapshot();
    if (!payload) return;
    const parentId = inputDatasetId;
    await runTask(async () => {
      const result = await applyDatasetFix(payload, { action, numComparatorHandling, ...extra });
      await setDataset(result, {
        parentId,
        action: `fix: ${action}`,
        tab: 'validate',
        message: 'Fixed TAME generated'
      });
      const review = await reviewDataset(currentDataset());
      validationReview = review;
      dataset = { ...dataset, issues: review.issues || [] };
      persistActiveDataset();
      await showTab('validate');
      message = result.operationSummary || '표준화를 완료했습니다. 다음 업무의 입력 자료를 갱신했습니다.';
    });
  }

  async function applyTagPlacement(mode) {
    const payload = inputSnapshot();
    if (!payload) return;
    const parentId = inputDatasetId;
    await runTask(async () => {
      const result = await applyDatasetTagPlacement(payload, { mode });
      await setDataset(result, {
        parentId,
        action: `tags: ${mode}`,
        tab: mode === 'meta' ? 'meta' : 'data',
        message: mode === 'meta' ? 'Tags moved to META' : 'Tags moved to DATA headers'
      });
    });
  }

  async function executeMetaAction(action) {
    const payload = inputSnapshot();
    if (!payload || !action?.name) return;
    const parentId = inputDatasetId;
    await runTask(async () => {
      const result = await applyDatasetAction(payload, { action: action.name });
      await setDataset(result, {
        parentId,
        action: `action: ${action.name}`,
        tab: 'data',
        message: `${action.label || action.name} applied`
      });
    });
  }

  async function executeMetaActionPipeline(pipeline) {
    const payload = inputSnapshot();
    if (!payload || !pipeline?.name) return;
    const parentId = inputDatasetId;
    await runTask(async () => {
      const result = await applyDatasetActionPipeline(payload, { pipeline: pipeline.name });
      await setDataset(result, {
        parentId,
        action: `action pipeline: ${pipeline.name}`,
        tab: 'data',
        message: `${pipeline.name} pipeline applied`
      });
    });
  }

  async function executeMetaAnalysis(analysis) {
    const payload = inputSnapshot();
    if (!payload || !analysis?.name) return;
    if (['REFERENCE_INTERVAL','RI_EP28','REFERENCE_INTERVAL_EP28'].includes(analysis.plugin)) {await openReferenceSettings();return;}
    const allowPlugins = confirmPluginTrust(analysis);
    if (allowPlugins === null) return;
    const parentId = inputDatasetId;
    await runTask(async () => {
      const result = await runMetaAnalysis(payload, { analysis: analysis.name, allowPlugins });
      await setDataset(result, {
        parentId,
        action: `analysis: ${analysis.name}`,
        kind:'result',
        tab: result.charts?.length ? 'eda' : 'data',
        message: `${analysis.label || analysis.name} analysis generated`
      });
    });
  }

  async function executeFirstActionPipeline() {
    if (!metaActionPipelines.length) {
      error = '실행 가능한 전처리 묶음이 없습니다. META[ACTION_PIPELINES]가 있는 예제 파일을 열어 주세요.';
      return;
    }
    await executeMetaActionPipeline(metaActionPipelines[0]);
  }

  async function executeFirstMetaAnalysis() {
    if (inputSnapshot()?.dataContext?.hasAnalysisPlan) {await executePlannedAnalysis(); return;}
    if (!metaAnalyses.length) {
      error = '실행 가능한 분석 버튼이 없습니다. 분석 프리셋이 포함된 TAME 파일을 열어 주세요.';
      return;
    }
    await executeMetaAnalysis(metaAnalyses[0]);
  }

  async function applyPreset(preset) {
    const payload = inputSnapshot();
    if (!payload || !preset?.name) return;
    const parentId = inputDatasetId;
    await runTask(async () => {
      const result = await applyDatasetPreset(payload, { preset: preset.name });
      await setDataset(result, {
        parentId,
        action: `preset: ${preset.name}`,
        tab: 'data',
        message: `${preset.label || preset.name} applied`
      });
    });
  }

  async function executeAnonymize() {
    const payload = inputSnapshot();
    if (!payload) return;
    const parentId = inputDatasetId;
    await runTask(async () => {
      const result = await anonymizeDataset(payload, { hashTags, dropTags, salt });
      const mappingTables = Object.fromEntries(
        Object.entries(result.mappingTables || {}).map(([name, rows]) => [`mapping:${name}`, rows])
      );
      await setDataset(result, {
        parentId,
        action: 'anonymize',
        edaTables: mappingTables,
        tab: 'data',
        message: 'Anonymized TAME generated'
      });
      message = 'Anonymized dataset generated';
    });
  }

  function chartRows(chart) {
    return chart?.rows || [];
  }

  function chartSeries(chart) {
    const values = [...new Set(chartRows(chart).map((row) => row.series || ''))];
    return values.length ? values : [''];
  }

  function chartXValues(chart) {
    return [...new Set(chartRows(chart).map((row) => String(row.x)))];
  }

  function chartDomain(chart) {
    const values = chartRows(chart).flatMap(row => chart.type === 'interval' ? [row.low, row.high, row.lowCiLow, row.lowCiHigh, row.highCiLow, row.highCiHigh] : [row.y]).filter(v => v !== null && v !== undefined).map(Number).filter(Number.isFinite);
    if (!values.length) return { min: 0, max: 1 };
    const min = Math.min(0, ...values);
    const max = Math.max(0, ...values);
    if (min === max) return { min: min - 1, max: max + 1 };
    return { min, max };
  }

  function chartGeometry(chart) {
    const width = 760;
    const height = 300;
    const margin = { top: 22, right: 24, bottom: 62, left: 68 };
    const innerWidth = width - margin.left - margin.right;
    const innerHeight = height - margin.top - margin.bottom;
    const domain = chartDomain(chart);
    const xValues = chartXValues(chart);
    const series = chartSeries(chart);
    return { width, height, margin, innerWidth, innerHeight, domain, xValues, series };
  }

  function chartX(chart, x) {
    const geometry = chartGeometry(chart);
    const index = Math.max(0, geometry.xValues.indexOf(String(x)));
    if (geometry.xValues.length <= 1) return geometry.margin.left + geometry.innerWidth / 2;
    return geometry.margin.left + (index / (geometry.xValues.length - 1)) * geometry.innerWidth;
  }

  function chartY(chart, y) {
    const geometry = chartGeometry(chart);
    const value = Number(y);
    return (
      geometry.margin.top +
      ((geometry.domain.max - value) / (geometry.domain.max - geometry.domain.min)) * geometry.innerHeight
    );
  }

  function barRects(chart) {
    const geometry = chartGeometry(chart);
    const groupWidth = geometry.innerWidth / Math.max(1, geometry.xValues.length);
    const barWidth = Math.max(3, (groupWidth / Math.max(1, geometry.series.length)) * 0.72);
    const zeroY = chartY(chart, 0);
    return chartRows(chart).map((row) => {
      const xIndex = Math.max(0, geometry.xValues.indexOf(String(row.x)));
      const sIndex = Math.max(0, geometry.series.indexOf(row.series || ''));
      const center = geometry.margin.left + groupWidth * xIndex + groupWidth / 2;
      const x = center - (barWidth * geometry.series.length) / 2 + sIndex * barWidth;
      const y = chartY(chart, row.y);
      return {
        ...row,
        label: row.x,
        value: row.y,
        x,
        y: Math.min(y, zeroY),
        width: Math.max(2, barWidth - 2),
        height: Math.max(1, Math.abs(zeroY - y)),
        color: chartColor(row.series || '', sIndex)
      };
    });
  }

  function linePath(chart, series) {
    const points = chartRows(chart)
      .filter((row) => (row.series || '') === series)
      .map((row) => [chartX(chart, row.x), chartY(chart, row.y)]);
    return points.map((point, index) => `${index === 0 ? 'M' : 'L'}${point[0]},${point[1]}`).join(' ');
  }

  function chartColor(series, index) {
    const palette = ['#2563eb', '#059669', '#dc2626', '#7c3aed', '#c2410c', '#0891b2', '#4b5563'];
    return palette[index % palette.length];
  }

  function chartXTicks(chart) {
    const geometry = chartGeometry(chart);
    const step = Math.max(1, Math.ceil(geometry.xValues.length / 8));
    return geometry.xValues.filter((_, index) => index % step === 0 || index === geometry.xValues.length - 1);
  }

  function chartYTicks(chart) {
    const domain = chartDomain(chart);
    return [domain.min, domain.min + (domain.max - domain.min) / 2, domain.max];
  }

  function formatChartNumber(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return '';
    if (Math.abs(number) >= 100) return number.toFixed(0);
    if (Math.abs(number) >= 10) return number.toFixed(1);
    return number.toFixed(2);
  }

  async function runTask(task, label = '작업을 실행하고 있습니다.') {
    if (busy) return;
    busy = true; busyLabel = label; error = ''; failureAuditFile = null;
    await tick();
    try {await task();}
    catch (err) {failureAuditFile = err.auditFile || null; error = err.message || String(err);message = '작업을 완료하지 못했습니다.';}
    finally {busy = false;}
  }

  function onDrop(event) {
    event.preventDefault();
    const file = event.dataTransfer?.files?.[0];
    if (file) loadFile(file);
  }
</script>

<div class="app-shell" role="application" aria-label="tametools 임상데이터 작업실" on:drop={onDrop} on:dragover|preventDefault>
  <header class="titlebar"><strong>tametools <small>0.4.0</small> · 임상데이터 작업실</strong><button on:click={() => showTab('tutorial')} disabled={busy}>도움말</button></header>
  <nav class="ribbon" aria-label="파일 작업">
    <button on:click={() => fileInput.click()} disabled={busy}>파일 열기</button>
    <button on:click={() => showExamples = !showExamples} disabled={busy} aria-expanded={showExamples}>공개 예제 불러오기</button>
    <button on:click={() => download('tame')} disabled={!dataset || busy}>TAME 저장</button>
    <button on:click={() => download('xlsx')} disabled={!dataset || busy}>Excel 저장</button>
    <button on:click={() => showProperties = !showProperties} aria-expanded={showProperties}>속성·플러그인</button>
    <span class="ribbon-note">저장 대상: {activeItem?.title || '열린 파일 없음'}</span>
    <input bind:this={fileInput} type="file" accept=".xlsx,.xlsm,.tame,.data.tame" hidden on:change={handleFileChange} />
  </nav>
  <div class="feedback" class:failed={Boolean(error)} class:running={busy} role={error ? 'alert' : 'status'} aria-live={error ? 'assertive' : 'polite'}>
    {#if busy}<span class="spinner" aria-hidden="true"></span><strong>{busyLabel}</strong>
    {:else if error}<strong>작업 실패</strong><span>{error}</span>{#if failureAuditFile}<button on:click={() => downloadAnalysisFile(failureAuditFile)}>실패 실행 기록 저장</button>{/if}<button on:click={() => error = ''} aria-label="오류 메시지 닫기">닫기</button>
    {:else}<span>{message}</span>{/if}
  </div>
  <div class="input-context">
    <strong>작업 입력</strong>
    <select aria-label="작업 입력 자료" bind:value={inputDatasetId} disabled={busy || !workspaceItems.some(item => item.kind === 'input')}>
      {#if !inputDatasetId}<option value="">자료를 불러오세요</option>{/if}
      {#each workspaceItems.filter(item => item.kind === 'input') as item}<option value={item.id}>{item.title} · {item.dataset.rowCount.toLocaleString()}행</option>{/each}
    </select>
    {#if activeItem?.kind === 'result'}<span>분석 결과를 보고 있습니다. 다음 업무는 위 입력 자료에 실행됩니다.</span>{/if}
  </div>
  {#if showExamples}<section class="example-section"><div class="block-header"><h2>공개 예제 불러오기</h2><button on:click={() => showExamples = false}>접기</button></div><ExampleLoader disabled={busy} on:loaded={openExampleDataset} on:error={event => error = event.detail} /></section>{/if}
  <div class="workspace" class:without-properties={!showProperties}>
    <aside class="sidebar">
      <section class="panel-section">
        <h2>작업 이력</h2>
        {#if workspaceItems.length}
          <div class="dataset-list">
            {#each workspaceItems as item, index}
              <div class="dataset-node-row">
                <button
                  type="button"
                  class="dataset-node"
                  class:active={item.id === activeDatasetId}
                  on:click={() => activateWorkspaceItem(item.id)}
                  disabled={busy}
                  title={item.title}
                >
                  <span class="dataset-title">{index + 1}. {item.title}</span>
                  <span class="dataset-meta">{item.kind === 'result' ? '결과' : '입력'} · {item.action}{item.parentId ? ' ← '+(workspaceItems.find(parent => parent.id === item.parentId)?.title || '삭제된 상위 자료') : ''}</span>
                  <span class="dataset-meta">
                    {item.dataset.rowCount.toLocaleString()}행 · {item.dataset.columns.length}열
                  </span>
                </button>
                <button
                  type="button"
                  class="dataset-delete"
                  on:click={() => deleteWorkspaceItem(item.id)}
                  disabled={busy}
                  title="Remove from chain"
                  aria-label={`Remove ${item.title} from TAME chain`}
                >
                  x
                </button>
              </div>
            {/each}
          </div>
        {:else}
          <div class="subtle">아직 열린 데이터가 없습니다.</div>
        {/if}
      </section>

      <section class="panel-section">
        <h2>파일 가져오기</h2>
        <div class="drop-zone">`.xlsx` 또는 `.tame` 파일을 여기에 놓으세요</div>
      </section>

      <section class="panel-section wizard-section">
        <h2>업무 단계</h2>
        <div class="subtle">위에 표시된 작업 입력을 기준으로 실행합니다. 필요한 단계만 선택할 수 있습니다.</div>
        <div class="wizard-list">
          {#each wizardItems as item}
            <button type="button" class="wizard-button" on:click={item.run} disabled={!inputDataset || busy}>
              <span>{item.title}</span>
              <span>{item.detail}</span>
            </button>
          {/each}
        </div>
      </section>

<details class="advanced-operations"><summary>추가 명령·프리셋</summary>
      <section class="panel-section">
        <h2>고급 명령</h2>
        <div class="command-list">
          {#each commandItems as command}
            <button type="button" class="command-button" on:click={command.run} disabled={!dataset || busy}>
              <span>{command.label}</span>
              <span>{command.mutates ? '새 TAME 자료 생성' : '자료 확인'}</span>
            </button>
          {/each}
        </div>
      </section>

      {#if presetList.length}
        <section class="panel-section">
          <h2>프리셋</h2>
          <div class="command-list">
            {#each presetList as preset}
              <button
                type="button"
                class="command-button"
                on:click={() => applyPreset(preset)}
                disabled={!dataset || busy}
                title={preset.description}
              >
                <span>{preset.label || preset.name}</span>
                <span>태그와 분석 정의 추가</span>
              </button>
            {/each}
          </div>
        </section>
      {/if}

      {#if metaActions.length}
        <section class="panel-section">
          <h2>저장된 전처리</h2>
          <div class="command-list">
            {#each metaActions as action}
              <button
                type="button"
                class="command-button"
                on:click={() => executeMetaAction(action)}
                disabled={!dataset || busy}
                title={action.description || action.type}
              >
                <span>{action.label || action.name}</span>
                <span>{action.mutates ? '새 TAME 자료 생성' : action.type}</span>
              </button>
            {/each}
          </div>
        </section>
      {/if}

      {#if metaActionPipelines.length}
        <section class="panel-section">
          <h2>전처리 묶음</h2>
          <div class="command-list">
            {#each metaActionPipelines as pipeline}
              <button
                type="button"
                class="command-button"
                on:click={() => executeMetaActionPipeline(pipeline)}
                disabled={!dataset || busy}
                title={(pipeline.actions || []).join(' -> ')}
              >
                <span>{pipeline.name}</span>
                <span>{(pipeline.actions || []).join(' -> ')}</span>
              </button>
            {/each}
          </div>
        </section>
      {/if}

      {#if metaAnalyses.length}
        <section class="panel-section">
          <h2>저장된 분석</h2>
          <div class="command-list">
            {#each metaAnalyses as analysis}
              <button
                type="button"
                class="command-button"
                on:click={() => executeMetaAnalysis(analysis)}
                disabled={!dataset || busy}
                title={analysis.description || analysis.plugin}
              >
                <span>{analysis.label || analysis.name}</span>
                <span>{analysis.plugin}</span>
              </button>
            {/each}
          </div>
        </section>
      {/if}

</details>
      {#if workbook}
        <section class="panel-section">
          <h2>Excel 시트 검토</h2>
          <div class="field">
            <label for="conversion-mode">변환 방식</label>
            <select id="conversion-mode" bind:value={conversionMode}>
              <option value="single">시트 하나를 데이터로 사용</option>
              <option value="merge">선택한 시트 병합</option>
            </select>
          </div>
          <button type="button" on:click={selectAllDataSheets}>데이터 시트 모두 선택</button>
        </section>
        <section class="panel-section">
          <h2>워크시트</h2>
          <div class="sheet-list">
            {#each workbook.sheets as sheet}
              <label class:control={sheet.isControl} class="sheet-row">
                <input
                  type={conversionMode === 'single' ? 'radio' : 'checkbox'}
                  checked={selectedSheets.includes(sheet.name)}
                  on:change={() => toggleSheet(sheet.name)}
                />
                <span>
                  <span class="sheet-name">{sheet.name}</span>
                  <span class="sheet-meta">
                    <span>{sheet.dataRows} data rows</span>
                    <span>{sheet.columns} cols</span>
                  </span>
                </span>
              </label>
              {#if sheet.warnings.length}
                <ul class="warning-list">
                  {#each sheet.warnings as warning}
                    <li>{warning}</li>
                  {/each}
                </ul>
              {/if}
            {/each}
          </div>
          <button type="button" on:click={convertSelectedSheets} disabled={!selectedSheets.length || busy}>
            Convert to TAME workspace
          </button>
        </section>
      {/if}

      {#if dataset}
        <section class="panel-section">
          <h2>Dataset</h2>
          <div>{rowStatus}</div>
          <div class="contract-grid">
            <div class="contract-card">
              <span>RESULT</span>
              <strong>{resultColumns.length}</strong>
              <small>{roleSummary.multiResult ? 'per-result analysis' : 'single or none'}</small>
            </div>
            <div class="contract-card">
              <span>ID roles</span>
              <strong>{qualifiedIds.length}</strong>
              <small>{qualifiedIds.map((item) => item.tags.join('/')).filter(Boolean).join(', ') || 'none'}</small>
            </div>
            <div class="contract-card">
              <span>PIVOT_CONTEXT</span>
              <strong>{pivotContextColumns.length}</strong>
              <small>{pivotContextColumns.map((item) => item.name).join(', ') || 'none'}</small>
            </div>
            <div class="contract-card">
              <span>Custom tags</span>
              <strong>{roleSummary.customTagCount || tagDefinitions.length}</strong>
              <small>{tagDefinitions.map((item) => item.name).join(', ') || 'none'}</small>
            </div>
          </div>
          {#if ageColumns.length}
            <div class="subtle">
              AGE bins: {ageColumns.map((item) => `${item.name} ${item.ageBinWidth || 10}y`).join(', ')}
            </div>
          {/if}
          {#if dataset.truncated}
            <p class="warning-list">Grid preview is limited to {dataset.visibleRowCount} rows.</p>
          {/if}
        </section>
        <section class="panel-section">
          <h2>Validation</h2>
          <button type="button" on:click={executeValidate} disabled={busy}>태그 검증</button>
          {#if validationIssues.length}
            <ul class="issue-list">
              {#each validationIssues.slice(0, 10) as issue}
                <li>row {issue.row}, {issue.column}: {issue.message}</li>
              {/each}
            </ul>
          {:else}
            <div>태그 규칙 위반이 없습니다. 표준화 필요 여부는 값 분포에서 확인하세요.</div>
          {/if}
        </section>
        {#if dataset.warnings.length}
          <section class="panel-section">
            <h2>주의사항</h2>
            <ul class="warning-list">
              {#each dataset.warnings as warning}
                <li>{warning}</li>
              {/each}
            </ul>
          </section>
        {/if}
      {/if}
    </aside>

    <main class="main-pane">
      {#if inputDataset?.dataContext?.warnings?.length}<details class="context-notice" open><summary>자료 해석 안내</summary>{#each inputDataset.dataContext.warnings as warning}<p>{warning}</p>{/each}</details>{/if}
      {#if inputDataset?.dataContext?.hasAnalysisPlan}<div class="plan-action"><span>대상자 조건·가중치·정책이 META에 저장되어 있습니다.</span><button on:click={executePlannedAnalysis} disabled={busy}>저장된 분석 실행</button></div>{/if}

      <div class="tabs">
        <button class:active={activeTab === 'data'} class="tab" type="button" on:click={() => showTab('data')}>
          데이터
        </button>
        <button class:active={activeTab === 'meta'} class="tab" type="button" on:click={() => showTab('meta')}>
          메타
        </button>
        <button class:active={activeTab === 'history'} class="tab" type="button" on:click={() => showTab('history')}>분석 이력</button>
        <button class:active={activeTab === 'validate'} class="tab" type="button" on:click={() => showTab('validate')}>
          검증
        </button>
        <button class:active={activeTab === 'eda'} class="tab" type="button" on:click={() => showTab('eda')}>
          분석결과
        </button>
        <button
          class:active={activeTab === 'tutorial'}
          class="tab"
          type="button"
          on:click={() => showTab('tutorial')}
        >
          도움말
        </button>
      </div>

      <div class="tab-content">
        <div class="grid-host ag-theme-quartz" class:concealed={activeTab !== 'data' || !dataset} bind:this={gridHost}></div>
        {#if activeTab === 'data' && !dataset}
          <div class="empty-state"><h2>파일 또는 공개 예제로 시작하세요</h2><p>Excel·TAME 파일을 불러와 검증, 탐색 분석, EP28 참고구간, 보고서 저장을 한 작업실에서 실행합니다.</p><button on:click={() => showExamples = true}>공개 예제 선택</button></div>
        {:else if activeTab === 'ri-settings'}
          <ReferencePanel dataset={inputDataset} {busy} on:run={event => executeReferenceInterval(event.detail)} />
        {:else if activeTab === 'sex-settings'}
          <SexNormalizationPanel dataset={inputDataset} {busy} on:run={event => applyValidationFix('normalize-sex-by-source', {sexNormalization:event.detail})} />
        {:else if activeTab === 'num-settings'}
          <section class="reference-panel"><h2>부등호 처리 정책</h2><p>입력: {inputItem?.title}</p><p>검출한계 미만·초과 결과의 원문과 행을 보존하고, 탐색 분석에서 사용할 정책을 META에 저장합니다. EP28 분석에서는 별도의 검열값 정책을 확인합니다.</p>
            <label for="num-comparator-handling">분석 정책</label><select id="num-comparator-handling" bind:value={numComparatorHandling}><option value="">정책을 선택하세요</option><option value="exclude">분석에서 제외 · 원문과 행 보존</option><option value="value">경계값으로 분석 · 원문과 행 보존</option></select>
            <button on:click={() => applyValidationFix('comparator-policy')} disabled={busy || !numComparatorHandling}>부등호 정책 저장</button>
          </section>
        {:else if activeTab === 'meta'}
          {#if dataset}
            <textarea class="meta-editor" bind:value={metaText} on:input={() => persistActiveDataset()} spellcheck="false"></textarea>
          {:else}
            <div class="empty-state">데이터를 열면 META 내용이 표시됩니다.</div>
          {/if}
        {:else if activeTab === 'history'}
          <LogPanel dataset={dataset ? {...dataset, metaText} : null} disabled={busy} />
        {:else if activeTab === 'validate'}
          <div class="validate-pane">
            <div class="table-block">
              <h3>검증 항목</h3>
              {#if validationIssues.length}
                <div class="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>행</th>
                        <th>중요도</th>
                        <th>열</th>
                        <th>태그</th>
                        <th>값</th>
                        <th>내용</th>
                      </tr>
                    </thead>
                    <tbody>
                      {#each validationIssues as issue}
                        <tr>
                          <td>{issue.row}</td>
                          <td>{issue.severity || 'error'}</td>
                          <td>{issue.column}</td>
                          <td>{issue.tag}</td>
                          <td>{issue.value}</td>
                          <td>{issue.message}</td>
                        </tr>
                      {/each}
                    </tbody>
                  </table>
                </div>
              {:else}
                <div>태그 규칙 위반이 없습니다. 표준화 필요 여부는 값 분포에서 확인하세요.</div>
              {/if}
            </div>

            {#if validationProfiles.length}
              {#each validationProfiles as profile}
                <div class="table-block">
                  <div class="block-header">
                    <h3>{profile.column} [[{profile.tag}]]</h3>
                    {#if profile.tag === 'SEX' && profile.hasSuggestedChanges}
                      <button type="button" on:click={openSexSettings} disabled={busy}>
                        Standardize SEX
                      </button>
                    {/if}
                    {#if profile.tag === 'AGE' && profile.hasSuggestedChanges}
                      <button type="button" on:click={() => applyValidationFix('standardize-age')} disabled={busy}>
                        Standardize AGE
                      </button>
                    {/if}
                  </div>
                  {#if profile.standard}
                    <div class="profile-standard">
                      {profile.standard.system || ''}
                      {profile.standard.defaultUnit ? ` / default ${profile.standard.defaultUnit}` : ''}
                      {profile.standard.allowedUnits?.length
                        ? ` (${profile.standard.allowedUnits.map((unit) => `${unit.code} ${unit.label}`).join(', ')})`
                        : ''}
                      {profile.standard.storage ? ` / ${profile.standard.storage}` : ''}
                    </div>
                  {/if}
                  {#if profile.distributions?.length}
                    {#each profile.distributions as distribution}
                      <div class="band-values">
                        <strong>{distribution.label}</strong>
                        {#each distribution.bands.slice(0, 10) as band}
                          <span>{band.band}: {band.count}</span>
                        {/each}
                      </div>
                    {/each}
                  {/if}
                  {#if profile.categories.length > 10}<p class="subtle">상위 10개 범주를 표시합니다. 전체 값은 데이터와 저장 파일에서 확인하세요.</p>{/if}
                  <div class="profile-list">
                    {#each profile.categories.slice(0, 10) as category}
                      <div class="profile-row">
                        <div class="profile-total">
                          <strong>{category.canonical}</strong>
                          <span>{category.total}</span>
                        </div>
                        <div class="raw-values">
                          {#each category.rawValues.slice(0, 10) as raw}
                            <span class:convertible={raw.willChange}>
                              {raw.raw}{raw.canonical && raw.canonical !== raw.raw
                                ? ` -> ${raw.canonical}`
                                : raw.parsed
                                  ? ` -> ${raw.parsed}`
                                  : ''}: {raw.count}
                            </span>
                          {/each}
                        </div>
                      </div>
                    {/each}
                  </div>
                  {#if profile.unrecognized.length}
                    <div class="unrecognized-values">
                      {#each profile.unrecognized as raw}
                        <span>{raw.raw}: {raw.count}</span>
                      {/each}
                    </div>
                  {/if}
                </div>
              {/each}
            {:else}
              <div class="empty-state">상태 확인 또는 오류 검증을 실행하면 태그 기반 값 분포가 표시됩니다.</div>
            {/if}
          </div>
        {:else if activeTab === 'eda'}
          <div class="eda-pane">
            {#if dataset?.resultType === 'reference_interval'}
              <section class="ri-summary"><h2>EP28 참고구간 결과</h2><p>입력: {dataset.analysisSource} · {referenceRows.length.toLocaleString()}개 결과. 화면은 최대 소수 4자리로 표시하며 저장 파일은 계산 정밀도를 유지합니다.</p>
                <p class="execution-settings">{settingsSummary(dataset.analysisSettings, referenceRows)}</p>
                {#if commonRiNotes.length}<div class="context-notice common-ri-notes"><strong>모든 결과에 공통인 해석 주의사항</strong><ul>{#each commonRiNotes as note}<li>{referenceNote({notes:note})}</li>{/each}</ul></div>{/if}
                <div class="report-actions">{#each dataset.reportFiles || [] as file}<button on:click={() => saveReport(file)} disabled={busy}>{file.label} 저장</button>{/each}</div>
                <div class="table-wrap"><table><thead><tr><th>검사 / 단위</th><th>집단</th><th>방법</th><th>n</th><th>참고구간</th><th>하한 CI</th><th>상한 CI</th><th>상태·해석</th></tr></thead><tbody>
                {#each referenceRows.slice(0,100) as row}<tr><td>{testLabel(row, dataset.testLabels)}<small>{displayUnit(row.unit)}</small></td><td>{groupLabel(row.group)}</td><td>{methodLabels[row.method] || row.method}</td><td>{row.n}</td><td>{interval(row.ref_low,row.ref_high)}</td><td>{interval(row.low_ci_low,row.low_ci_high)}</td><td>{interval(row.high_ci_low,row.high_ci_high)}</td><td><strong>{statusLabels[row.status] || row.status}</strong><small>{referenceNote(row, commonRiNotes)}</small></td></tr>{/each}
                </tbody></table></div>
                {#if referenceRows.length > 100}<p>첫 100행을 표시합니다. 아래 전체 상세 결과 또는 저장 파일에서 나머지를 확인하세요.</p>{/if}
              </section>
            {/if}

            {#if dataset?.charts?.length}
              <div class="chart-grid">
                {#each dataset.charts as chart}
                  <div class="chart-block">
                    <h3>{chart.title}</h3>
                    {#if chart.rows.length}
                      <svg
                        class="chart-canvas"
                        viewBox={`0 0 ${chartGeometry(chart).width} ${chartGeometry(chart).height}`}
                        role="img"
                        aria-label={chart.title}
                      >
                        <line
                          class="chart-axis"
                          x1={chartGeometry(chart).margin.left}
                          y1={chartY(chart, 0)}
                          x2={chartGeometry(chart).width - chartGeometry(chart).margin.right}
                          y2={chartY(chart, 0)}
                        />
                        <line
                          class="chart-axis"
                          x1={chartGeometry(chart).margin.left}
                          y1={chartGeometry(chart).margin.top}
                          x2={chartGeometry(chart).margin.left}
                          y2={chartGeometry(chart).height - chartGeometry(chart).margin.bottom}
                        />
                        {#each chartYTicks(chart) as tick}
                          <line
                            class="chart-gridline"
                            x1={chartGeometry(chart).margin.left}
                            y1={chartY(chart, tick)}
                            x2={chartGeometry(chart).width - chartGeometry(chart).margin.right}
                            y2={chartY(chart, tick)}
                          />
                          <text class="chart-y-label" x={chartGeometry(chart).margin.left - 8} y={chartY(chart, tick) + 4}>
                            {formatChartNumber(tick)}
                          </text>
                        {/each}
                        {#if chart.type === 'interval'}
                          {#each chartRows(chart) as point}
                            {#if point.low !== null && point.high !== null}
                              <g class="ri-interval-mark">{#if chart.y !== 'ref_high'}<circle cx={chartX(chart,point.x)} cy={chartY(chart,point.y)} r="4" fill="#2563eb" />{/if}<title>{groupLabel(point.x)}: {interval(point.low,point.high)} · 하한 CI {interval(point.lowCiLow,point.lowCiHigh)} · 상한 CI {interval(point.highCiLow,point.highCiHigh)}</title>
                                {#if point.lowCiLow != null && point.lowCiHigh != null}<line x1={chartX(chart,point.x)-7} x2={chartX(chart,point.x)-7} y1={chartY(chart,point.lowCiLow)} y2={chartY(chart,point.lowCiHigh)} stroke="#9bbadc" stroke-width="5" />{/if}
                                {#if point.highCiLow != null && point.highCiHigh != null}<line x1={chartX(chart,point.x)+7} x2={chartX(chart,point.x)+7} y1={chartY(chart,point.highCiLow)} y2={chartY(chart,point.highCiHigh)} stroke="#9bbadc" stroke-width="5" />{/if}
                                <line x1={chartX(chart,point.x)} x2={chartX(chart,point.x)} y1={chartY(chart,point.low)} y2={chartY(chart,point.high)} stroke="#2563eb" stroke-width="3" />
                                <line x1={chartX(chart,point.x)-9} x2={chartX(chart,point.x)+9} y1={chartY(chart,point.low)} y2={chartY(chart,point.low)} stroke="#2563eb" stroke-width="2" />
                                <line x1={chartX(chart,point.x)-9} x2={chartX(chart,point.x)+9} y1={chartY(chart,point.high)} y2={chartY(chart,point.high)} stroke="#2563eb" stroke-width="2" />
                              </g>
                            {/if}
                          {/each}
                        {:else if chart.type === 'line'}
                          {#each chartSeries(chart) as series, seriesIndex}
                            <path
                              class="chart-line"
                              d={linePath(chart, series)}
                              stroke={chartColor(series, seriesIndex)}
                            />
                            {#each chartRows(chart).filter((row) => (row.series || '') === series) as point}
                              <circle
                                cx={chartX(chart, point.x)}
                                cy={chartY(chart, point.y)}
                                r="3"
                                fill={chartColor(series, seriesIndex)}
                              />
                            {/each}
                          {/each}
                        {:else if chart.type === 'scatter'}
                          {#each chartRows(chart) as point}
                            <circle cx={chartX(chart, point.x)} cy={chartY(chart, point.y)} r="4" fill="#2563eb" />
                          {/each}
                        {:else}
                          {#each barRects(chart) as bar}
                            <rect
                              x={bar.x}
                              y={bar.y}
                              width={bar.width}
                              height={bar.height}
                              fill={bar.color}
                            >
                              <title>{bar.label}: {formatChartNumber(bar.value)}</title>
                            </rect>
                          {/each}
                        {/if}
                        {#each chartXTicks(chart) as tick}
                          <text class="chart-x-label" x={chartX(chart, tick)} y={chartGeometry(chart).height - 18}>
                            {chart.type === 'interval' ? groupLabel(tick) : tick}
                          </text>
                        {/each}
                      </svg>
                      {#if chart.type === 'interval'}<p class="subtle">{chart.y === 'ref_high' ? '진한 선: 하한–상한 · 옅은 선: 각 한계의 신뢰구간' : '점: 가중 평균 · 선: 95% 신뢰구간'} · 선 위에서 수치 확인</p>{/if}
                      {#if chartSeries(chart).filter(Boolean).length > 1}
                        <div class="chart-legend">
                          {#each chartSeries(chart).filter(Boolean) as series, seriesIndex}
                            <span><i style={`background:${chartColor(series, seriesIndex)}`}></i>{series}</span>
                          {/each}
                        </div>
                      {/if}
                    {:else}
                      <div>표시할 그래프가 없습니다.</div>
                    {/if}
                  </div>
                {/each}
              </div>
            {/if}
            {#if visibleWarnings.length}
              <details class="table-block analysis-warnings">
                <summary>해석 주의사항 · {visibleWarnings.length}개</summary>
                <ul class="warning-list">
                  {#each visibleWarnings as warning}
                    <li>{warning}</li>
                  {/each}
                </ul>
              </details>
            {/if}
            {#each Object.entries(dataset?.analysisTableFiles || {}) as [name, info]}<RemoteResultTable {name} {info} />{/each}
            {#each Object.entries(edaTables) as [name, rows]}
              <details class="result-details" open={dataset?.resultType !== 'reference_interval' && ['summary','describe','survey_means'].includes(name)}><summary>{name} · {rows.length.toLocaleString()}행</summary><ResultTable {name} {rows} labels={dataset?.testLabels || {}} /></details>
            {/each}
            {#if !Object.keys(edaTables).length && !dataset?.charts?.length}
              <div class="empty-state">탐색 분석 또는 비식별화를 실행하면 결과 표가 표시됩니다.</div>
            {/if}
          </div>
        {:else if activeTab === 'tutorial'}
          <div class="tutorial-pane">
            <h2>빠른 사용 순서</h2><ol><li>공개 예제 불러오기에서 NHANES 또는 Kenya를 선택합니다.</li><li>업무 단계의 검증·표준화 확인과 탐색 분석을 실행합니다.</li><li>분석 결과가 선택되어 있어도 작업 입력은 유지됩니다. EP28 참고구간을 열고 조건을 선택해 계산합니다.</li><li>NHANES의 가중 분석은 저장된 분석 실행으로 확인합니다. 일반 인구 자료의 후보 구간은 임상 참고구간이 아닙니다.</li><li>원본과 결과의 메타에서 LOG·설정을 확인하고 TAME, Excel, Word 보고서를 저장합니다.</li></ol>
            <h2>웹 작업 흐름</h2>
            <ol>
              <li>Excel 파일을 열고 Excel 시트 검토에서 단일 시트 또는 여러 시트 병합을 선택한다.</li>
              <li>변환 전 경고를 확인한 뒤 작업실로 가져오기를 실행한다.</li>
              <li>데이터 탭에서 스프레드시트처럼 값을 검토하고, 상단 속성·플러그인을 열어 열 선택 패널에서 태그와 COLUMN META를 함께 지정한다.</li>
              <li>ID는 가능하면 ID(patient), ID(sample), ID(hospital)처럼 qualifier를 붙여 join/익명화/분석 의도를 분리한다.</li>
              <li>Wide RESULT 컬럼은 PIVOT_CONTEXT에 TESTNAME, UNIT, REF_LOW, REF_HIGH를 넣어 후속 이상 플래그가 항목별 참고치를 쓰게 한다.</li>
              <li>TAG_DEFINITIONS에서 AGE5처럼 AGE를 상속한 새 태그를 만들고 AGE_BIN_WIDTH 같은 분석 속성을 추가한다.</li>
              <li>메타 탭에서 SETTINGS, COLUMN, TAG_DEFINITIONS, ACTIONS 같은 TOML 메타데이터를 확인하거나 수정한다.</li>
              <li>탐색 분석으로 태그 기반 검증과 탐색적 분석표를 생성한다.</li>
              <li>EP28 참고구간에서 설정을 확인한 뒤 참고치 분석 결과 TAME 파일을 생성한다.</li>
              <li>ID, ID(patient), ID(hospital), NAME 태그를 지정한 뒤 개인정보 비식별화로 익명화된 TAME 데이터를 만든다.</li>
              <li>좌측 작업 이력에서 원본과 파생 TAME 파일을 전환한다. 분석 이력 탭에서 단계별 코드표·건수·설정을 확인하고 현재 값으로 기록 검증을 실행한다.</li>
              <li>TAME 저장 또는 Excel 저장으로 현재 선택된 TAME 파일을 저장한다.</li>
            </ol>
            <h2>권장 태그</h2>
            <p>
              결과값은 RESULT::NUM 또는 RESULT::&lt;NUM&gt;, 카테고리 분포는 CATEGORY, 그룹별 결과 요약은 BY,
              성별은 SEX::CATEGORY::BY, 나이는 AGE, 검사항목은 TESTNAME::CATEGORY 또는 ITEM::CATEGORY,
              익명화 대상은 ID(patient)/ID(hospital)/NAME으로 지정한다. 여러 RESULT 컬럼이 있으면 플러그인은 각 RESULT를
              독립적으로 처리하고, PIVOT_CONTEXT가 있으면 컬럼별 참고치로 판정한다.
            </p>
          </div>
        {/if}
      </div>
    </main>

    <aside class="properties" class:concealed={!showProperties}>
      <section class="panel-section">
        <h2>열 선택</h2>
        {#if dataset}
          <div class="column-list">
            {#each dataset.columns as column}
              <button
                type="button"
                class="column-item"
                class:active={selectedField === column.field}
                on:click={() => selectColumn(column.field)}
                title={taggedHeader(column)}
              >
                <span>{column.metadata?.LABEL || column.name}</span>
                <span class="column-tags">
                  {column.tags.length ? `tags: ${column.tags.join('::')}` : 'untagged'}
                  {dataset.tagStorage === 'meta' ? ' / stored in META' : ''}
                </span>
              </button>
            {/each}
          </div>
        {:else}
          <div>열이 없습니다.</div>
        {/if}
      </section>

      <section class="panel-section">
        <h2>열 태그·메타데이터</h2>
        {#if selectedColumn}
          <div class="field">
            <label for="column-name">열 이름</label>
            <input id="column-name" bind:value={columnNameDraft} />
          </div>
          <div class="field">
            <label for="column-tags">태그 · 쉼표로 구분</label>
            <input id="column-tags" bind:value={tagsDraft} />
          </div>
          <div class="quick-tags">
            <button type="button" on:click={() => addTag('ID(patient)')}>ID(patient)</button>
            <button type="button" on:click={() => addTag('ID(sample)')}>ID(sample)</button>
            <button type="button" on:click={() => addTag('RESULT')}>RESULT</button>
            <button type="button" on:click={() => addTag('<NUM>')}>&lt;NUM&gt;</button>
            <button type="button" on:click={() => addTag('REF_LOW')}>REF_LOW</button>
            <button type="button" on:click={() => addTag('REF_HIGH')}>REF_HIGH</button>
          </div>
          <div class="field">
            <label for="column-label">표시 이름</label>
            <input id="column-label" bind:value={columnLabelDraft} />
          </div>
          <div class="field">
            <label for="column-description">설명</label>
            <textarea id="column-description" class="small-textarea" bind:value={columnDescriptionDraft}></textarea>
          </div>
          <div class="two-col">
            <div class="field">
              <label for="column-unit">단위</label>
              <input id="column-unit" bind:value={columnUnitDraft} />
            </div>
            <div class="field">
              <label for="column-ucum">UCUM 단위</label>
              <input id="column-ucum" bind:value={columnUcumUnitDraft} />
            </div>
          </div>
          <div class="two-col">
            <div class="field">
              <label for="column-loinc">LOINC</label>
              <input id="column-loinc" bind:value={columnLoincDraft} />
            </div>
            <div class="field">
              <label for="column-local-code">로컬 코드</label>
              <input id="column-local-code" bind:value={columnLocalCodeDraft} />
            </div>
          </div>
          <label class="check-row">
            <input type="checkbox" bind:checked={columnPhiDraft} />
            <span>개인정보·식별자 표시</span>
          </label>
          <div class="context-box">
            <div class="context-title">PIVOT_CONTEXT</div>
            <div class="subtle">Wide RESULT 컬럼별 항목명, 단위, 참고치를 저장합니다.</div>
            <div class="two-col">
              <div class="field">
                <label for="pivot-test">검사항목명</label>
                <input id="pivot-test" bind:value={pivotTestNameDraft} placeholder={columnNameDraft} />
              </div>
              <div class="field">
                <label for="pivot-unit">단위</label>
                <input id="pivot-unit" bind:value={pivotUnitDraft} />
              </div>
            </div>
            <div class="two-col">
              <div class="field">
                <label for="pivot-low">Ref low</label>
                <input id="pivot-low" bind:value={pivotRefLowDraft} />
              </div>
              <div class="field">
                <label for="pivot-high">Ref high</label>
                <input id="pivot-high" bind:value={pivotRefHighDraft} />
              </div>
            </div>
          </div>
          <div class="tag-palette">
            {#if tagCatalog.length}
              {#each tagCatalog as group}
                <div class="tag-group">
                  <div class="tag-group-title" title={group.description}>{group.label}</div>
                  <div class="tag-group-buttons">
                    {#each leafTagNames(group) as tag}
                      <button class="tag-button" type="button" on:click={() => addTag(tag)}>{tag}</button>
                    {/each}
                  </div>
                </div>
              {/each}
            {:else}
              {#each tagPalette as tag}
                <button class="tag-button" type="button" on:click={() => addTag(tag)}>{tag}</button>
              {/each}
            {/if}
          </div>
          <button type="button" on:click={applyColumnMeta}>열 태그·META 적용</button>
        {:else}
          <div>열을 선택하세요.</div>
        {/if}
      </section>

      <section class="panel-section">
        <h2>TAG_DEFINITIONS</h2>
        {#if tagDefinitions.length}
          <div class="tag-definition-list">
            {#each tagDefinitions as definition}
              <button
                type="button"
                class="definition-chip"
                on:click={() => {
                  tagDefinitionName = definition.name;
                  tagDefinitionLabel = definition.label || definition.name;
                  tagDefinitionDescription = definition.description || '';
                  tagDefinitionInherits = (definition.inherits || []).join(', ');
                  tagDefinitionAgeBinWidth = definition.ageBinWidth ? String(definition.ageBinWidth) : '';
                }}
              >
                <span>{definition.name}</span>
                <small>{(definition.inherits || []).join(', ') || 'no inheritance'}</small>
              </button>
            {/each}
          </div>
        {/if}
        <div class="field">
          <label for="tag-definition-name">새 태그 이름</label>
          <input id="tag-definition-name" bind:value={tagDefinitionName} />
        </div>
        <div class="field">
          <label for="tag-definition-label">표시 이름</label>
          <input id="tag-definition-label" bind:value={tagDefinitionLabel} />
        </div>
        <div class="field">
          <label for="tag-definition-inherits">상속할 태그</label>
          <input id="tag-definition-inherits" bind:value={tagDefinitionInherits} />
        </div>
        <div class="field">
          <label for="tag-definition-age-bin">AGE_BIN_WIDTH</label>
          <input id="tag-definition-age-bin" bind:value={tagDefinitionAgeBinWidth} />
        </div>
        <div class="field">
          <label for="tag-definition-description">설명</label>
          <textarea id="tag-definition-description" class="small-textarea" bind:value={tagDefinitionDescription}></textarea>
        </div>
        <button type="button" on:click={applyTagDefinition} disabled={!dataset || busy}>태그 정의 적용</button>
      </section>

      <section class="panel-section">
        <h2>성별 코드 설정</h2>
        <p>출처마다 다른 숫자 코드를 표준 성별 열로 변환합니다.</p>
        <button on:click={openSexSettings} disabled={!inputDataset || busy}>출처별 성별 정규화</button>
      </section>

      <section class="panel-section">
        <h2>플러그인</h2>
        {#if pluginList.length}
          <div class="field">
            <label for="plugin-select">플러그인 선택</label>
            <select id="plugin-select" bind:value={selectedPlugin} on:change={() => pluginOptionsText = '{}'}>
              {#each pluginList as plugin}
                <option value={plugin.name}>{plugin.name}{plugin.name === 'REFERENCE_INTERVAL' ? ' (구형)' : ''}{plugin.requiresTrust ? ' (신뢰 확인 필요)' : ''}</option>
              {/each}
            </select>
          </div>
          <div class="role-contract">
            <strong>필요한 열 역할</strong>
            <span>{selectedPluginRoleText(selectedPluginSpec)}</span>
          </div>
          <div class="field">
            <label for="plugin-options">추가 설정 JSON</label>
            <textarea id="plugin-options" class="plugin-editor small" bind:value={pluginOptionsText} spellcheck="false"></textarea>
          </div>
          <button type="button" on:click={executePlugin} disabled={!inputDataset || busy || !selectedPlugin || Boolean(roleProblem(selectedPluginSpec))} title={roleProblem(selectedPluginSpec)}>플러그인 실행</button>
        {:else}
          <div>사용 가능한 플러그인이 없습니다.</div>
        {/if}
        <details><summary>사용자 플러그인 작성</summary><p>입력한 Python 코드는 이 PC에서 실행됩니다.</p>
        <div class="field">
          <label for="plugin-name">이름</label>
          <input id="plugin-name" bind:value={pluginName} />
        </div>
        <div class="field">
          <label for="plugin-description">설명</label>
          <input id="plugin-description" bind:value={pluginDescription} />
        </div>
        <div class="field">
          <label for="plugin-source">Python</label>
          <textarea id="plugin-source" class="plugin-editor" bind:value={pluginSource} spellcheck="false"></textarea>
        </div>
        <button type="button" on:click={createPlugin} disabled={!dataset || busy}>플러그인 생성</button></details>
      </section>

      <section class="panel-section">
        <h2>비식별화</h2>
        <div class="field">
          <label for="hash-tags">해시 처리할 태그</label>
          <input id="hash-tags" bind:value={hashTags} />
        </div>
        <div class="field">
          <label for="drop-tags">제거할 태그</label>
          <input id="drop-tags" bind:value={dropTags} />
        </div>
        <div class="field">
          <label for="salt">Salt</label>
          <input id="salt" bind:value={salt} />
        </div>
        <button type="button" on:click={executeAnonymize} disabled={!dataset || busy}>비식별 자료 생성</button>
      </section>
    </aside>
  </div>

  <footer class="statusbar">
    <span>{activeItem ? activeItem.title : rowStatus}</span>
    {#if error}<span class="error">{error}</span>{/if}
  </footer>
</div>
