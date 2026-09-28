# Agent Development Notes

- Use `DefaultAzureCredential`/managed identity; never add API keys or secrets to source or `agent.yaml`.
- Infrastructure lives in Bicep (`infra/`), one resource group per stage; deploy through `azd`.
- The hosted agent (`src/agent`) is published by `scripts/deploy_agent.py`; the ACA web app (`src/web`) is deployed by azd.
- Do not set platform-reserved variables (`FOUNDRY_*`, `AGENT_*`, `PORT`, `HOME`, `APPLICATIONINSIGHTS_CONNECTION_STRING`) on the hosted agent.
- Keep `name` in `src/agent/agent.yaml` in sync with `agentName` in `infra/main.bicep`.
- Run `python -m pytest -q` and `az bicep build --file infra/main.bicep` after changes.
- If you are in VS Code, read the vscode-microsoft-foundry skill first.
