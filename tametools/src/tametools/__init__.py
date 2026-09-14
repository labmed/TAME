from __future__ import annotations

from ._version import __version__
from importlib import import_module
from typing import Any


_EXPORTS = {
    "ActionError": ".actions",
    "AGE_CANONICAL_UNITS": ".age",
    "AGE_DEFAULT_UNIT": ".age",
    "AGE_UCUM_SYSTEM": ".age",
    "CommandPipelineError": ".command_pipelines",
    "EmbeddedFunctionError": ".embedded_functions",
    "EmbeddedFunctionsDisabledError": ".embedded_functions",
    "EvaluationReport": ".evaluation",
    "NULL": ".cellstate",
    "SEX_CANONICAL_VALUES": ".sex",
    "TameDataset": ".models",
    "TameError": ".exceptions",
    "TameFormatError": ".exceptions",
    "TameImportError": ".exceptions",
    "TameTagError": ".exceptions",
    "TameValidationError": ".exceptions",
    "action_payloads": ".actions",
    "action_pipeline_payloads": ".actions",
    "age_band_bounds": ".age",
    "age_band_label": ".age",
    "age_value_profile": ".age",
    "anonymize_dataset": ".transforms",
    "append_log_entry": ".provenance",
    "apply_clinical_chemistry_preset": ".presets",
    "apply_preset": ".presets",
    "available_action_pipelines": ".actions",
    "available_actions": ".actions",
    "available_command_pipelines": ".command_pipelines",
    "available_export_formats": ".exporters",
    "available_presets": ".presets",
    "available_works": ".pipeline",
    "category_distribution_table": ".analysis",
    "category_value_profile": ".review_profiles",
    "cell_state": ".cellstate",
    "dataset_with_tags_in_headers": ".tag_placement",
    "dataset_with_tags_in_meta": ".tag_placement",
    "datetime_value_profile": ".review_profiles",
    "describe_dataset": ".analysis",
    "embed_image_columns": ".images",
    "evaluate_dataset": ".evaluation",
    "execute_action": ".actions",
    "execute_action_pipeline": ".actions",
    "execute_command_pipeline": ".command_pipelines",
    "execute_work": ".pipeline",
    "export_dataset": ".exporters",
    "exploratory_data_analysis": ".analysis",
    "extract_image_columns": ".images",
    "fix_num_comparator_values": ".transforms",
    "flat_tag_names": ".tag_catalog",
    "harmonize_comparator_thresholds": ".transforms",
    "import_xlsx_into_tame": ".io",
    "list_plugins": ".plugin_base",
    "merge_datasets": ".merge",
    "normalize_sex": ".sex",
    "numeric_percentile_band_table": ".analysis",
    "numeric_percentile_table": ".analysis",
    "parse_age": ".age",
    "parse_age_to_years": ".age",
    "parse_datetime_value": ".review_profiles",
    "parse_serialized_cell": ".cellstate",
    "parse_sex_binary_map": ".sex",
    "parse_temporal_value": ".review_profiles",
    "preset_payloads": ".presets",
    "read_tame": ".io",
    "read_xlsx": ".io",
    "reference_interval_plan": ".analysis",
    "result_by_summary_table": ".analysis",
    "run_embedded_function": ".embedded_functions",
    "run_plugin": ".plugin_base",
    "sample_dataset": ".transforms",
    "serialize_cell": ".cellstate",
    "sex_value_profile": ".sex",
    "split_comparator_columns": ".transforms",
    "standardize_age_dataset": ".age",
    "standardize_age_value": ".age",
    "standardize_sex_dataset": ".sex",
    "normalize_sex_by_source": ".sex_normalization",
    "sex_normalization_preview": ".sex_normalization",
    "tag_catalog_payload": ".tag_catalog",
    "tag_placement_table": ".tag_placement",
    "tags_to_header": ".tag_placement",
    "tags_to_meta": ".tag_placement",
    "validate_dataset": ".analysis",
    "write_data_tame": ".io",
    "write_evaluation_report": ".evaluation",
    "write_mapping_tables": ".transforms",
    "write_meta_tame": ".io",
    "write_tame": ".io",
    "write_xlsx": ".io",
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module 'tametools' has no attribute {name!r}")
    module = import_module(module_name, __name__)
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
