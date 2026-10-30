from nexctf.plugins import InputType, Plugin, TypeDef

from nexctf_sandbox._sandbox import CONFIG
from nexctf_sandbox.solutions.runner import (
    RunnerSolution,
    RunnerSolutionCreate,
    RunnerSolutionRead,
    RunnerSolutionUpdate,
)
from nexctf_sandbox.solutions.script import (
    ScriptSolution,
    ScriptSolutionCreate,
    ScriptSolutionRead,
    ScriptSolutionUpdate,
)

plugin = Plugin(
    solution_types=[
        TypeDef(
            "runner",
            model=RunnerSolution,
            create_schema=RunnerSolutionCreate,
            update_schema=RunnerSolutionUpdate,
            read_schema=RunnerSolutionRead,
            compatible_input_types=[InputType.CODE],
            description="Code runner — executes the submitted Python 3 code against a set of test cases.",
        ),
        TypeDef(
            "script",
            model=ScriptSolution,
            create_schema=ScriptSolutionCreate,
            update_schema=ScriptSolutionUpdate,
            read_schema=ScriptSolutionRead,
            compatible_input_types=[InputType.INPUT, InputType.TEXT, InputType.CODE],
            description="Script checker — runs a custom Python function check(answer, team_id, team_fields) → bool to validate the answer.",
        ),
    ],
    config=CONFIG,
)
