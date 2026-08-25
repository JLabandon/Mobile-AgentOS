# Mobile AgentOS

Mobile AgentOS is a graph-centered runtime for app-oriented mobile agents on Android. One persistent `Global Execution Graph` stores every active User Task, executable `WORK`, dependency-bearing `Artifact`, and task-scoped completion condition.

```text
User Goal -> Planner -> GraphSteward <- AppAgents
                           ^              ^
                           |              |
                    GraphScheduler -------+
```

`GraphSteward` is the sole writer of the global graph. Planner submits coarse graph fragments; GraphSteward maps them into globally unique nodes and resolves exact Artifact identities; GraphScheduler assigns ready `WORK` under hard resource constraints; AppAgents execute continuous app-level jobs and submit completion, failure, or runtime expansion proposals.

`ArtifactNode` is the authoritative result representation. A derived `ArtifactIndex` maps exact, schema-normalized keys to active nodes, enabling completed-result reuse and in-flight producer coalescing without maintaining a second artifact store. `TaskRecord` projects each User Task onto the shared graph and allows disconnected or shared task components.

## Layout

- `mobile_agent_os/graph_space`: global graph schema, Registry Table, ArtifactIndex, GraphSteward, events, and checkpoints.
- `mobile_agent_os/scheduling`: FIFO ready-frontier scheduling and lease-based Resource Table.
- `mobile_agent_os/execution`: assignment-driven AppAgent and generic primitive UI loop.
- `mobile_agent_os/planner`: User Goal plus Registry Table to a graph fragment.
- `mobile_agent_os/model_clients`: OpenAI, Gemini, and DeepSeek model adapters.
- `mobile_agent_os/android`: ADB and UI primitives.
- `benchmarks/fixtures`: controlled Android app fixtures.
- `tests/unit` and `tests/fakes`: isolated protocol and component tests.
- `verification/l2` and `verification/l3`: API-backed semantic probes and full-runtime simulation kept outside the production package.

## Models

The default text and screen model is Gemini `gemini-3.5-flash-lite`. Model clients read credentials from `GEMINI_API_KEY` or `GOOGLE_API_KEY`, `OPENAI_API_KEY`, and `DEEPSEEK_API_KEY`. Set `MOBILE_AGENT_OS_TEXT_MODEL_PROVIDER` or `MOBILE_AGENT_OS_SCREEN_MODEL_PROVIDER` to select `openai`, `gemini`, or `deepseek` where supported.

## Verification

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m pytest -q -p no:cacheprovider
python -m verification.l2.run
python -m verification.l3.run
```

L1 runs locally. L2 invokes the production Planner and component protocols. L3 starts from natural-language User Goals and replaces Android UI execution with a generic sleep executor while retaining the production Planner, GraphSteward, GraphScheduler, AppAgent, Artifact, and evaluation path.
