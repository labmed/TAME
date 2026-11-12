# tametools tutorials

Run from the repository root after installing `./tametools[analysis,nhanes,integration,web]`.

- [Tagged EDA](01_tagged_eda/README.md)
- [Anonymization](02_anonymize/README.md)
- [Comparator values](03_comparator_handling/README.md)
- [Merging sources](04_merge_multihospital/README.md)
- [Plugins](05_plugins/README.md)
- [Value states](06_value_states/README.md)
- [Multiple sheets](07_multisheet_storage/README.md)
- [Image columns](08_image_columns/README.md)
- [Export to R](09_export_for_r/README.md)
- [Command chains](11_command_pipelines/README.md)
- [Hands-on workflow](16_hands_on_guide/README.md)
- [Extended tags](17_tag_spec_extension/README.md)
- [Laboratory turnaround-time plugin](19_lab_tat_plugin/README.md)
- [EP28 reference intervals](25_reference_interval_ep28/README.md)
- [Accumulating QC results: `sync` and `WESTGARD_QC`](26_qc_sync/README.md)

For public examples, open **공개 예제 불러오기** in the [web workbench](../docs/WEB_QUICKSTART_KO.md). NHANES downloads are optional; the Kenya example is included with attribution. Generated reports belong in a new `outputs/` directory.

## QC 플러그인 연결

[QC 연결 예제](../docs/PLUGIN_WORKFLOWS_KO.md)는 기본 전처리 후 정밀도·관리규칙·추세·run 검토를 연결하고 공통 HTML/Word 보고서를 생성합니다. 입력은 `web/backend/examples/qc_workflow.tame`입니다.
