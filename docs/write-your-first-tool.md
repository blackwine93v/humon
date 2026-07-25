# Write your first tool

A Humon tool is a small class implementing the `Tool` protocol. It can live in the Humon
repo (built-in) or in your own pip package (third-party). Both are discovered the same
way — via the `humon.tools` entry-point group — and both must be explicitly enabled in
config before they do anything.

## 1. Scaffold

```bash
humon new-tool weather
```

This creates `humon-tool-weather/` with a package, an entry-point registration, and a
starter test.

## 2. Implement the protocol

```python
from humon.core.interfaces import ToolContext, ToolResult


class WeatherTool:
    name = "weather"                       # unique, snake_case
    description = "Get the current weather for a city."   # shown to the model
    input_schema = {                       # JSON Schema for the model
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"],
        "additionalProperties": False,
    }
    permissions = ["net.read"]             # least privilege; the policy engine decides

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        city = str(args.get("city", ""))
        # ... do the work (async I/O only) ...
        return {"ok": True, "content": f"Sunny in {city}.", "error": None}
```

Rules to honour (see [`../CLAUDE.md`](../CLAUDE.md)):

- **Declare the least privilege you need.** You never authorize yourself — the policy
  engine maps your `permissions` to allow/deny/require_approval.
- **Read your config from `ctx.config`**, your jail from `ctx.jail_paths`. You cannot see
  other tools' config.
- **Treat all external input as untrusted.** Cap your output size.
- **Need human sign-off mid-execution?** `await ctx.request_approval("summary")`.

## 3. Register the entry point

In your `pyproject.toml` (the scaffold does this):

```toml
[project.entry-points."humon.tools"]
weather = "humon_tool_weather.tool:WeatherTool"
```

## 4. Install, enable, test

```bash
pip install -e .           # makes it discoverable — NOT active
```

Add to `config.yaml` (this is what activates it):

```yaml
tools:
  weather:
    enabled: true
policy:
  rules:
    net.read: allow
```

Restart Humon; `!tools` now lists `weather`, and the model can call it.

## 5. Ship tests

Add unit tests (the scaffold includes one) and security regression tests for any guard
your tool enforces. Run the full gate before committing.
