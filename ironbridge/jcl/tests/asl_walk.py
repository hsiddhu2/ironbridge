"""Offline walker for the ASL subset ironbridge emits (Pass, Task, Choice). Test use only.

It evaluates the real JSONata strings with jsonata-python. Lambda tasks return the RC from
`simulate`, like the stub. It is not a Step Functions emulator.
"""

from __future__ import annotations

import jsonata


def _eval(value, env: dict):
    if isinstance(value, str) and value.startswith("{%") and value.endswith("%}"):
        return jsonata.Jsonata(value[2:-2]).evaluate(None, env)
    if isinstance(value, dict):
        return {k: _eval(v, env) for k, v in value.items()}
    if isinstance(value, list):
        return [_eval(v, env) for v in value]
    return value


def walk(asl: dict, execution_input: dict, max_states: int = 500) -> tuple[list[str], object]:
    """Return the state names entered and the final output."""
    variables: dict = {}
    data = execution_input
    name = asl["StartAt"]
    path = []
    context = {"Execution": {"Id": "local", "RoleArn": "local-role"}}
    for _ in range(max_states):
        path.append(name)
        st = asl["States"][name]
        env = {**variables, "states": {"input": data, "context": context}}
        result = None
        if st["Type"] == "Task":
            payload = _eval(st["Arguments"]["Payload"], env)
            rc = (payload.get("simulate") or {}).get(payload["step"], 0)
            result = {"Payload": {"rc": rc}}
            env["states"]["result"] = result
        if st["Type"] == "Choice":
            nxt = next(
                (c["Next"] for c in st["Choices"] if _eval(c["Condition"], env)), st["Default"]
            )
        else:
            nxt = st.get("Next")
        new_vars = _eval(st.get("Assign", {}), env)
        data = _eval(st["Output"], env) if "Output" in st else (result or data)
        variables.update(new_vars)
        if st.get("End"):
            return path, data
        name = nxt
    raise RuntimeError("state limit reached")
