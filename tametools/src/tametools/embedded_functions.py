from __future__ import annotations

import csv
import inspect
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
from typing import Any

import pandas as pd

from .cellstate import serialize_cell
from .config import ci_get
from .io import read_tame
from .models import OperationOutput, TameDataset
from .tags import build_header, merge_tags, parse_header
from .toml_compat import dumps as dumps_toml


class EmbeddedFunctionError(RuntimeError):
    pass


class EmbeddedFunctionsDisabledError(PermissionError):
    pass


def run_embedded_function(
    dataset: TameDataset,
    meta: dict | None,
    step_name: str,
    options: dict,
    *,
    allow_functions: bool = False,
) -> OperationOutput | None:
    spec = _resolve_function_spec(meta, step_name, options)
    if spec is None:
        return None
    if not allow_functions:
        raise EmbeddedFunctionsDisabledError(
            "Embedded functions are disabled. Re-run with allow_functions=True or use `tametools run --allow-functions`."
        )

    if spec["lang"] == "python":
        return _run_python_function(dataset, spec, step_name, options)
    if spec["lang"] == "r":
        return _run_r_function(dataset, spec, step_name, options)
    raise EmbeddedFunctionError(
        f"Unsupported embedded function language '{spec['lang']}' for function '{spec['name']}'."
    )


def _resolve_function_spec(meta: dict | None, step_name: str, options: dict) -> dict[str, Any] | None:
    functions = ci_get(meta, "FUNCTIONS", {})
    if not isinstance(functions, dict):
        return None

    explicit_name = ci_get(options, "FUNCTION", None)
    if explicit_name is not None:
        function_name = str(explicit_name).strip()
        config = ci_get(functions, function_name, None)
        if not isinstance(config, dict):
            raise EmbeddedFunctionError(f"Embedded function '{function_name}' is not defined in META[FUNCTIONS].")
    else:
        function_name = str(step_name).strip()
        config = ci_get(functions, function_name, None)
        if not isinstance(config, dict):
            return None

    source = ci_get(config, "SOURCE", "")
    if not str(source).strip():
        raise EmbeddedFunctionError(f"Embedded function '{function_name}' is missing SOURCE.")

    language = str(ci_get(config, "LANG", "python")).strip().lower()
    if language == "py":
        language = "python"
    return {
        "name": function_name,
        "entry": str(ci_get(config, "ENTRY", "run")).strip() or "run",
        "kind": str(ci_get(config, "KIND", "dataset")).strip().lower() or "dataset",
        "lang": language,
        "source": str(source),
    }


def _run_python_function(dataset: TameDataset, spec: dict[str, Any], step_name: str, options: dict) -> OperationOutput:
    api = _python_api()
    namespace = {
        "__builtins__": __builtins__,
        "OperationOutput": OperationOutput,
        "TameDataset": TameDataset,
        "build_header": build_header,
        "ci_get": ci_get,
        "merge_tags": merge_tags,
        "parse_header": parse_header,
        "pd": pd,
    }
    try:
        exec(spec["source"], namespace, namespace)
    except Exception as exc:
        raise EmbeddedFunctionError(f"Failed to compile embedded Python function '{spec['name']}': {exc}") from exc

    handler = namespace.get(spec["entry"])
    if not callable(handler):
        raise EmbeddedFunctionError(
            f"Embedded Python function '{spec['name']}' does not define callable entry '{spec['entry']}'."
        )

    values = {
        "dataset": dataset,
        "options": options,
        "api": api,
        "step_name": step_name,
    }
    try:
        result = _invoke_python_handler(handler, values)
    except Exception as exc:
        raise EmbeddedFunctionError(f"Embedded Python function '{spec['name']}' failed: {exc}") from exc
    return _coerce_embedded_result(result, dataset, step_name, spec)


def _run_r_function(dataset: TameDataset, spec: dict[str, Any], step_name: str, options: dict) -> OperationOutput:
    rscript = shutil.which("Rscript")
    if rscript is None:
        raise EmbeddedFunctionError(
            f"Embedded R function '{spec['name']}' requires `Rscript`, but it was not found on PATH."
        )

    with tempfile.TemporaryDirectory(prefix="tametools_udf_") as tmpdir:
        workdir = Path(tmpdir)
        source_path = workdir / "function.R"
        runner_path = workdir / "runner.R"
        input_path = workdir / "input.tsv"
        output_path = workdir / "output.tsv"
        options_path = workdir / "options.json"
        meta_path = workdir / "meta.toml"
        schema_path = workdir / "schema.toml"
        job_path = workdir / "job.toml"
        output_meta_path = workdir / "output.meta.toml"
        output_schema_path = workdir / "output.schema.toml"
        output_job_path = workdir / "output.job.toml"
        message_path = workdir / "message.txt"
        warnings_path = workdir / "warnings.txt"

        source_path.write_text(spec["source"], encoding="utf-8")
        runner_path.write_text(_r_runner_script(), encoding="utf-8")
        _write_tagged_tsv(dataset, input_path)
        options_path.write_text(json.dumps(options, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        meta_path.write_text(_control_text(dataset, "META"), encoding="utf-8")
        schema_path.write_text(_control_text(dataset, "SCHEMA"), encoding="utf-8")
        job_path.write_text(_control_text(dataset, "JOB"), encoding="utf-8")

        command = [
            rscript,
            str(runner_path),
            str(source_path),
            str(spec["entry"]),
            str(input_path),
            str(output_path),
            str(options_path),
            str(meta_path),
            str(schema_path),
            str(job_path),
            str(output_meta_path),
            str(output_schema_path),
            str(output_job_path),
            str(message_path),
            str(warnings_path),
            str(step_name),
        ]
        completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
        if completed.returncode != 0:
            stderr = (completed.stderr or "").strip()
            stdout = (completed.stdout or "").strip()
            detail = stderr or stdout or f"exit code {completed.returncode}"
            raise EmbeddedFunctionError(f"Embedded R function '{spec['name']}' failed: {detail}")
        if not output_path.exists():
            raise EmbeddedFunctionError(
                f"Embedded R function '{spec['name']}' did not write output TSV to '{output_path.name}'."
            )

        loaded = _read_r_output_dataset(
            output_path=output_path,
            dataset=dataset,
            output_meta_path=output_meta_path,
            output_schema_path=output_schema_path,
            output_job_path=output_job_path,
        )
        message = _optional_text(message_path)
        warnings = _optional_lines(warnings_path)
        return OperationOutput(
            name=step_name,
            dataset=loaded,
            warnings=warnings or None,
            message=message or f"embedded R function {spec['name']} completed",
        )


def _invoke_python_handler(handler: Any, values: dict[str, Any]) -> Any:
    signature = inspect.signature(handler)
    parameters = list(signature.parameters.values())
    if any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters):
        return handler(**values)

    accepted = {name: value for name, value in values.items() if name in signature.parameters}
    if accepted:
        return handler(**accepted)

    if any(parameter.kind == inspect.Parameter.VAR_POSITIONAL for parameter in parameters):
        return handler(values["dataset"], values["options"], values["api"], values["step_name"])

    positional_parameters = [
        parameter
        for parameter in parameters
        if parameter.kind in {inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD}
    ]
    positional_values = [values["dataset"], values["options"], values["api"], values["step_name"]]
    return handler(*positional_values[: len(positional_parameters)])


def _coerce_embedded_result(
    result: Any,
    dataset: TameDataset,
    step_name: str,
    spec: dict[str, Any],
) -> OperationOutput:
    if isinstance(result, OperationOutput):
        output = result
        if output.dataset is not None:
            output.dataset = _synchronize_control_sections(output.dataset)
        return output
    if isinstance(result, TameDataset):
        return OperationOutput(
            name=step_name,
            dataset=_synchronize_control_sections(result),
            message=f"embedded {spec['lang']} function {spec['name']} completed",
        )
    if isinstance(result, pd.DataFrame):
        transformed = dataset.replace(df=result)
        return OperationOutput(
            name=step_name,
            dataset=_synchronize_control_sections(transformed),
            message=f"embedded {spec['lang']} function {spec['name']} completed",
        )
    if isinstance(result, str):
        return OperationOutput(name=step_name, dataset=dataset, message=result)
    if result is None:
        return OperationOutput(
            name=step_name,
            dataset=dataset,
            message=f"embedded {spec['lang']} function {spec['name']} returned no dataset changes",
        )
    raise EmbeddedFunctionError(
        f"Embedded function '{spec['name']}' returned unsupported value type {type(result).__name__}."
    )


def _python_api() -> SimpleNamespace:
    return SimpleNamespace(
        OperationOutput=OperationOutput,
        TameDataset=TameDataset,
        build_header=build_header,
        ci_get=ci_get,
        merge_tags=merge_tags,
        parse_header=parse_header,
        pd=pd,
        replace_controls=_replace_controls,
        sync_controls=_synchronize_control_sections,
    )


def _replace_controls(
    dataset: TameDataset,
    *,
    meta: dict[str, Any] | None = None,
    schema: dict[str, Any] | None = None,
    job: dict[str, Any] | None = None,
) -> TameDataset:
    updated_meta = dataset.meta if meta is None else meta
    updated_schema = dataset.schema if schema is None else schema
    updated_job = dataset.job if job is None else job
    raw_sections = dict(dataset.raw_sections)
    _set_control_section(raw_sections, "META", updated_meta)
    _set_control_section(raw_sections, "SCHEMA", updated_schema)
    _set_control_section(raw_sections, "JOB", updated_job)
    return dataset.replace(meta=updated_meta, schema=updated_schema, job=updated_job, raw_sections=raw_sections)


def _synchronize_control_sections(dataset: TameDataset) -> TameDataset:
    return _replace_controls(dataset)


def _set_control_section(raw_sections: dict[str, str], name: str, value: dict[str, Any]) -> None:
    if value:
        raw_sections[name] = dumps_toml(value)
    else:
        raw_sections.pop(name, None)


def _write_tagged_tsv(dataset: TameDataset, path: str | Path) -> None:
    path = Path(path)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", quotechar='"', lineterminator="\n")
        writer.writerow(dataset.tagged_headers())
        settings = dataset.settings()
        for row in dataset.df.itertuples(index=False):
            writer.writerow([serialize_cell(value, settings, for_excel=False) for value in row])


def _control_text(dataset: TameDataset, section: str) -> str:
    text = dataset.raw_sections.get(section)
    if text is not None:
        return text
    if section == "META":
        return dumps_toml(dataset.meta)
    if section == "SCHEMA":
        return dumps_toml(dataset.schema)
    if section == "JOB":
        return dumps_toml(dataset.job)
    return ""


def _read_r_output_dataset(
    *,
    output_path: Path,
    dataset: TameDataset,
    output_meta_path: Path,
    output_schema_path: Path,
    output_job_path: Path,
) -> TameDataset:
    with tempfile.TemporaryDirectory(prefix="tametools_r_out_") as tmpdir:
        workdir = Path(tmpdir)
        data_path = workdir / "result.data.tame"
        meta_sidecar_path = workdir / "result.meta.tame"
        data_path.write_text(output_path.read_text(encoding="utf-8"), encoding="utf-8")
        meta_sidecar_path.write_text(
            _sidecar_text(
                meta_text=_optional_text(output_meta_path) or _control_text(dataset, "META"),
                schema_text=_optional_text(output_schema_path) or _control_text(dataset, "SCHEMA"),
                job_text=_optional_text(output_job_path) or _control_text(dataset, "JOB"),
            ),
            encoding="utf-8",
        )
        loaded = read_tame(data_path, meta_path=meta_sidecar_path)
        return loaded.replace(source_path=dataset.source_path)


def _sidecar_text(*, meta_text: str, schema_text: str, job_text: str) -> str:
    sections: list[str] = []
    if schema_text.strip():
        sections.append(f"<SCHEMA>\n{schema_text.rstrip()}\n</SCHEMA>")
    if job_text.strip():
        sections.append(f"<JOB>\n{job_text.rstrip()}\n</JOB>")
    if meta_text.strip():
        sections.append(f"<META>\n{meta_text.rstrip()}\n</META>")
    return "\n".join(sections) + ("\n" if sections else "")


def _optional_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def _optional_lines(path: Path) -> list[str]:
    text = _optional_text(path)
    return [line.strip() for line in text.splitlines() if line.strip()]


def _r_runner_script() -> str:
    return """args <- commandArgs(trailingOnly = TRUE)
source_path <- args[[1]]
entry_name <- args[[2]]
values <- list(
  input_path = args[[3]],
  output_path = args[[4]],
  options_path = args[[5]],
  meta_path = args[[6]],
  schema_path = args[[7]],
  job_path = args[[8]],
  output_meta_path = args[[9]],
  output_schema_path = args[[10]],
  output_job_path = args[[11]],
  message_path = args[[12]],
  warnings_path = args[[13]],
  step_name = args[[14]]
)

env <- new.env(parent = globalenv())
source(source_path, local = env, chdir = TRUE)
if (!exists(entry_name, envir = env, mode = "function")) {
  stop(sprintf("Embedded R function entry '%s' not found.", entry_name))
}

fun <- get(entry_name, envir = env, mode = "function")
formal_names <- names(formals(fun))
call_args <- values
if (!("..." %in% formal_names)) {
  call_args <- values[intersect(names(values), formal_names)]
}

result <- do.call(fun, call_args)
if (is.character(result) && length(result) == 1 && nzchar(result)) {
  writeLines(result, values$message_path, useBytes = TRUE)
}
"""
