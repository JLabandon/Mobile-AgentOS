# Mobile AgentOS

Mobile AgentOS is a graph-centered runtime for app-oriented mobile agents on Android.

```text
Planner -> Graph Space <- AppAgents
              ^
              |
          Scheduler
```

`Graph Space` contains the versioned Execution Graph, Registry Table, artifacts, and event log. `GraphSteward` is the sole writer. Planner creates the initial coarse graph; Scheduler assigns ready `WORK` nodes under Resource Table constraints; AppAgents execute one assignment at a time and submit completion, failure, or an atomic checkpoint-and-expand request.

The graph uses explicit `SOURCE`, `WORK`, and `SINK` nodes. A `WORK` node owns a complete AppAgent observe-think-act-settle loop. Cross-app handoff, resource boundaries, checkpoint recovery, and final evaluation remain visible in the graph; app-internal primitive UI actions remain inside the work unit.

## Layout

- `mobile_agent_os/graph_space`: graph models, Registry Table, versioned GraphSteward, artifacts, and events.
- `mobile_agent_os/scheduling`: FIFO ready-frontier scheduling and lease-based Resource Table.
- `mobile_agent_os/execution`: assignment-driven AppAgent and generic primitive UI loop.
- `mobile_agent_os/planner`: User Goal plus Registry Table to initial graph.
- `mobile_agent_os/model_clients`: OpenAI, Gemini, and DeepSeek model adapters.
- `mobile_agent_os/android`: ADB and UI primitives.
- `benchmarks/fixtures`: controlled Android app fixtures.
- `tests/unit` and `tests/fakes`: isolated protocol and component tests.

## Models

The default text and screen model is Gemini `gemini-3.5-flash-lite`. The key is loaded from `GEMINI_API_KEY`, `GOOGLE_API_KEY`, or `research_materials/markdown/mobile_agent_os/methods/Gemini API`; it is never copied into project source or configuration. Set `MOBILE_AGENT_OS_TEXT_MODEL_PROVIDER` or `MOBILE_AGENT_OS_SCREEN_MODEL_PROVIDER` to select `openai`, `gemini`, or `deepseek` where supported.
