"""One loop for every declared chain of steps: WORKS, PIPELINES and ACTION_PIPELINES.

The three differ in what a step is: a named step or plugin, a command line, a declared action. What happens
between two steps is the same for all of them, and is written once here so that it cannot drift apart again:

- every step runs in a recorded operation of its own, on the result of the step before it;
- a step that returns no dataset leaves the data as it was, and the next step receives that;
- the history of the data is carried along, and each step is logged if the caller asks for it;
- a step that could not be run (status ``SKIPPED``: nobody knows the name) ends the chain. The steps after it are
  not run on data that missed a step. The result still holds the record of the step that was skipped, and the
  caller decides what to make of it (the command line and sync refuse to save).
"""
from __future__ import annotations

from typing import Any, Callable, Iterable

from .audit import dataset_content_hash
from .models import OperationOutput, PipelineResult, TameDataset
from .provenance import inherit_logs, operation_scope, run_scope

Scope = Callable[[Any], tuple[str, Any]]                                              # step -> (operation name, its parameters)
Execute = Callable[[TameDataset, Any], OperationOutput]                              # (data so far, step) -> output
Record = Callable[[TameDataset, Any, OperationOutput, TameDataset, str], TameDataset]  # (result, step, output, input, input hash) -> logged result


def run_chain(dataset: TameDataset, name: str, steps: Iterable[Any], *, scope: Scope, execute: Execute,
              record: Record | None = None, hash_input: bool = False) -> PipelineResult:
    """Run ``steps`` one after the other, each on the result of the one before."""
    outputs: list[OperationOutput] = []
    current = dataset
    with run_scope():
        for step in steps:
            action, parameters = scope(step)
            with operation_scope(action, [current], parameters=parameters):
                input_hash = dataset_content_hash(current) if hash_input else ""
                output = execute(current, step)
                target = output.dataset if output.dataset is not None else current
                output.dataset = inherit_logs(target, current)
                if record is not None:
                    output.dataset = record(output.dataset, step, output, current, input_hash)
                outputs.append(output)
                current = output.dataset
            if output.status == "SKIPPED":
                break
    return PipelineResult(work_name=str(name), final_dataset=current, outputs=outputs)


def unfinished_steps(result: PipelineResult) -> list[str]:
    """Names of the steps of a chain that did not succeed (a chain stops at the first one that was skipped)."""
    return [str(output.name) for output in result.outputs if output.status != "SUCCEEDED"]
