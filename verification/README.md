# Verification Harnesses

The verification package stays outside `mobile_agent_os` and imports the production runtime as a client.

- `l2`: invokes the production Planner for semantic graph construction, then probes runtime expansion through the production AppAgent protocol.
- `l3`: starts from two natural-language User Goals and runs Planner, GraphSteward, GraphScheduler, AppAgents, Artifact reuse, and task evaluation. Android observation and input are replaced by a generic sleep executor.

Run from the project root:

```bash
python -m verification.l2.run
python -m verification.l3.run
```
