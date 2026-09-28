# Foundry hosted agent CI/CD sample (MAF multi-agent + Azure Container Apps)

This repository deploys a **Microsoft Agent Framework (MAF) multi-agent workflow** as a
**Microsoft Foundry hosted agent**. A **web front-end on Azure Container Apps (ACA)**
calls that agent. Each stage (`dev`, `test`, `prod`) gets its own **resource group**.
Each resource group has its own Foundry resource, Foundry project, model deployment,
container registry, Application Insights instance, and ACA app. GitHub Actions
promotes changes through the stages in order.

Authentication uses Microsoft Entra ID everywhere. The repository uses no API keys:

- Locally: `DefaultAzureCredential`.
- In CI: GitHub OIDC workload identity federation.
- Agent identity and Foundry project identity: managed identities.
- ACA app: a user-assigned managed identity.

## Use case: Contoso support desk

```mermaid
flowchart LR
    U[Customer] --> W[ACA web app<br/>FastAPI + chat UI]
    W -- Responses API<br/>managed identity --> H
    subgraph H[Foundry hosted agent: contoso-support-desk]
        T[triage] -->|billing| B[billing specialist]
        T -->|technical| X[technical specialist]
        T -->|otherwise| G[general specialist]
        B --> R[reviewer]
        X --> R
        G --> R
    end
    R --> W
```

| Agent | Role |
|-------|------|
| `triage` | Classifies the request as `billing`, `technical`, or `general`, then summarizes the key facts. |
| `billing` / `technical` / `general` | Draft a reply. MAF switch-case routing picks one of these three agents. |
| `reviewer` | Checks the draft for tone, safety, and completeness. It returns the only output of the workflow. |

The workflow is built with `WorkflowBuilder` in [src/agent/workflow.py](src/agent/workflow.py). It is
exposed as one agent with `.as_agent()` and served on the Foundry `responses` protocol by
`ResponsesHostServer`. The prompts are in [src/agent/prompts](src/agent/prompts).

## Prompt agents vs. hosted agents

Foundry Agent Service runs two kinds of agents:

| | Prompt agent | Hosted agent (this repo) |
|---|---|---|
| **What you deploy** | A declarative definition: model, instructions, and built-in tools (web search, file search, and so on). | A container image with your own agent code, built with any framework (MAF, LangGraph, custom). |
| **Where it runs** | Inside the Foundry service. You don't write any server code. | Foundry runs your container on managed compute (image from your ACR, listening on port 8088). |
| **Logic** | One model call loop: instructions + tools. | Anything you can code: multi-agent workflows, routing, custom tools, deterministic steps. |
| **Created with** | `PromptAgentDefinition` (see the reference repo [foundry-agent-cicd](https://github.com/sriaishwarya1709/foundry-agent-cicd)). | `HostedAgentDefinition` with a `ContainerConfiguration` image and protocol versions. |
| **Invoked with** | The project's Responses API with an `agent_reference`. | The agent's own endpoint, `.../agents/<name>/endpoint/protocols/openai/responses`, with an `agent_session_id`. |
| **Identity** | Runs as the project. | Each agent gets its own Entra ID *instance identity*, which needs roles such as Foundry User. |
| **Best for** | Single-purpose assistants with no custom code. | Orchestration, custom logic, or bringing an existing agent framework into Foundry. |

### Why the portal shows one agent

This is a **multi-agent** solution **packaged as a single hosted agent**. Foundry registers one
hosted agent, `contoso-support-desk`, because it deploys one container. The five MAF agents
(`triage`, `billing`, `technical`, `general`, `reviewer`) are `agent_framework.Agent` objects
wired together by `WorkflowBuilder` inside that container. They aren't separate Foundry resources.
Their activity shows up as spans in the traces (Application Insights / Foundry tracing), not as
separate entries in the agent list. This keeps the whole workflow versioned, promoted, and rolled
back as one unit.

If you need each agent to be managed separately in the portal, you have two options:

- Create each specialist as its own prompt agent and call them from the workflow.
- Deploy each one as its own hosted agent and connect them over A2A.

Both options add more versions to keep in sync across stages.

## End-to-end flow

### Request flow (runtime)

```mermaid
sequenceDiagram
    autonumber
    actor User
    participant Web as ACA web app (FastAPI)
    participant EP as Foundry agent endpoint
    participant HA as Hosted agent container (MAF)
    participant LLM as gpt-4.1-mini deployment
    participant AI as Application Insights

    User->>Web: POST /api/chat {message, session_id, previous_response_id}
    Web->>Web: Validate input, get Entra token (user-assigned managed identity)
    Web->>EP: responses.create(input, agent_session_id)
    EP->>HA: Route to the active version's container session
    HA->>LLM: triage agent classifies request
    HA->>HA: Switch-case edge picks billing / technical / general
    HA->>LLM: specialist agent drafts reply
    HA->>LLM: reviewer agent finalizes reply
    HA-->>EP: Final reply (Responses protocol)
    EP-->>Web: response.output_text, response.id
    Web-->>User: {reply, response_id}
    HA--)AI: Traces for each agent step
    Web--)AI: Request telemetry
```

1. The browser sends the message together with a `session_id` it generated. For follow-up turns it also sends the previous `response_id`.
2. The web app authenticates with its **user-assigned managed identity**, which has Foundry User on the account. It then calls the hosted agent's Responses endpoint through `AIProjectClient.get_openai_client(agent_name=...)`.
3. Foundry routes the request to the agent version that currently receives 100% of traffic. The `agent_session_id` keeps a conversation on the same container session.
4. Inside the container, the MAF workflow runs **triage → one specialist → reviewer**. Each agent calls the model through `FoundryChatClient`, using the **agent's instance identity**.
5. Only the reviewer's output is returned. Setting `previous_response_id` gives the next turn the conversation history.
6. Application Insights receives telemetry from both the web app and the hosted agent. Local authentication is disabled on App Insights, so both send it with Entra ID credentials.

### Delivery flow (CI/CD)

```mermaid
flowchart LR
    Dev[git push main] --> V[validate<br/>pytest + bicep build]
    V --> D[Deploy dev]
    D --> T[Deploy test]
    T --> P[Deploy prod]
    subgraph Stage[Each stage job: GitHub Environment + OIDC]
        direction TB
        S1[azd provision<br/>Bicep -> rg-hosted-agents-&lt;stage&gt;] --> S2[predeploy hook<br/>ACR build -> new hosted agent version<br/>-> RBAC -> wait active -> route 100%]
        S2 --> S3[azd deploy web<br/>ACA remote build + new revision]
        S3 --> S4[Smoke tests<br/>agent invoke + /healthz]
    end
    D -.-> Stage
```

The same commit moves through every stage. Each stage builds its own image in its own registry and
creates a new agent version in its own project. A stage runs only after the previous one succeeds
(and its approval, if you configured required reviewers).

## Per-stage architecture

```mermaid
flowchart TB
    subgraph RG["rg-&lt;prefix&gt;-&lt;stage&gt; (one per dev / test / prod)"]
        AIF[Foundry resource<br/>aif-*] --> P[Project proj-&lt;stage&gt;]
        AIF --> M[gpt-4.1-mini deployment]
        P --> HA[Hosted agent<br/>contoso-support-desk]
        ACR[Container registry cr*] -. image pull (project MI) .-> HA
        ACR -. image pull (UAMI) .-> CA
        CAE[ACA environment cae-*] --> CA[Container app ca-web-*]
        CA -- Foundry User --> HA
        LAW[Log Analytics] --- APPI[Application Insights]
        P -. connection .-> APPI
        CA -. telemetry .-> APPI
    end
```

| Identity | Role | Scope |
|----------|------|-------|
| Deployer (developer or GitHub OIDC identity) | Foundry User, AcrPush | Foundry account, registry |
| Foundry project managed identity | Foundry User, AcrPull, Monitoring Metrics Publisher | account, registry, App Insights |
| Hosted agent instance identity (assigned at deploy time) | Foundry User | Foundry account |
| ACA web user-assigned identity | Foundry User, AcrPull, Monitoring Metrics Publisher | account, registry, App Insights |

### How a deployment works

1. **Provision.** `azd provision` runs [infra/main.bicep](infra/main.bicep), which creates the resource group and every resource in it.
2. **Publish the hosted agent.** The `predeploy` hook runs [scripts/deploy_agent.py](scripts/deploy_agent.py). The script:
   - builds `src/agent` with ACR Tasks for `linux/amd64`, using a timestamped, content-hashed tag;
   - creates a new hosted agent version;
   - grants the agent's identity Foundry User;
   - waits for the version to become `active`;
   - routes 100% of traffic to the new version.

   If the agent source hasn't changed since the active version, the script skips all of this.
3. **Deploy the web app.** azd builds `src/web` with ACA remote build and rolls out a new container app revision. When you re-provision, the app keeps the image that's already deployed.

## Repository layout

```text
src/agent/            MAF multi-agent workflow, Dockerfile, agent.yaml (hosted agent)
src/web/              FastAPI chat front-end for Azure Container Apps
infra/                Per-stage Bicep: resource group, Foundry, ACR, ACA, monitoring, RBAC
infra/bootstrap/      One-time GitHub OIDC identity
scripts/              deploy_agent.py (build/publish/invoke), predeploy hook, GitHub bootstrap
tests/                Offline tests for workflow routing, web API, and deployment logic
.github/workflows/    Staged dev -> test -> prod pipeline (reusable per-stage workflow)
```

## Prerequisites

- An Azure subscription where you can create resources and role assignments.
- Azure CLI, a recent Azure Developer CLI (`azd`), PowerShell 7, Python 3.11 or later, and, for the CI setup, GitHub CLI.
- In `swedencentral` (the default) or your chosen region, hosted-agent support and Global Standard quota for `gpt-4.1-mini` (30K TPM per stage by default).

## Deploy one stage locally

```powershell
az login
azd auth login

azd env new hosted-agents-dev
azd env set AZURE_LOCATION swedencentral
azd env set AZURE_STAGE dev
azd env set AZURE_PRINCIPAL_ID (az ad signed-in-user show --query id -o tsv)
azd env set AZURE_PRINCIPAL_TYPE User

azd up
```

To add the other stages, repeat these steps with `hosted-agents-test` / `AZURE_STAGE test` and
`hosted-agents-prod` / `AZURE_STAGE prod`. Each azd environment maps to its own resource group,
`rg-<azd env name>`. Use `azd env select <name>` to switch between stages.

When `azd up` finishes, it prints the `web` endpoint. Open it and try these prompts:

- *I was charged twice for my subscription this month. Can I get a refund?* (routes to billing)
- *Our API calls started failing with HTTP 503 an hour ago.* (routes to technical)
- *Do you have an office in Stockholm?* (routes to general)

### Invoke the hosted agent directly

```powershell
azd env get-values | ForEach-Object {
  if ($_ -match '^([^=]+)="(.*)"$') { Set-Item "env:$($Matches[1])" $Matches[2] }
}
.\.venv\Scripts\python.exe scripts\deploy_agent.py invoke --prompt "My invoice shows the wrong plan."
```

### Run locally

```powershell
python -m venv .venv; .\.venv\Scripts\pip install -r requirements-dev.txt
azd env get-values > .env        # FOUNDRY_PROJECT_ENDPOINT, AZURE_AI_MODEL_DEPLOYMENT_NAME, ...
```

In VS Code, use the launch configurations:

- **Run hosted agent locally** serves the workflow at `http://localhost:8088/responses`.
- **Run web front-end locally** starts the UI on `:8000`. The UI calls the *deployed* hosted agent.

### Force a new agent version or clean up

```powershell
.\.venv\Scripts\python.exe scripts\deploy_agent.py deploy --force   # new version even if source unchanged
azd down --purge                                                    # removes the current stage's resource group
```

## GitHub Actions setup

The pipeline in [.github/workflows/deploy.yml](.github/workflows/deploy.yml) runs these jobs in order:

1. **validate**: pytest and `bicep build`.
2. **Deploy dev**
3. **Deploy test**
4. **Deploy prod**

Each deploy job calls the reusable workflow [.github/workflows/deploy-stage.yml](.github/workflows/deploy-stage.yml)
inside its own GitHub Environment. That workflow does the following:

- runs `azd up` against the stage's own azd environment and resource group;
- smoke-tests the hosted agent through the Responses API;
- smoke-tests the ACA `/healthz` endpoint.

### One-time bootstrap

GitHub can't authenticate to Azure until your tenant trusts the repository. You grant that trust once from your machine:

```powershell
az login
gh auth login
.\scripts\bootstrap-github.ps1 -SubscriptionId '<subscription-id>'
```

The script:

- creates a user-assigned managed identity in `rg-github-identities`;
- adds federated credentials for the `dev`, `test`, and `prod` GitHub Environments;
- grants that identity Contributor and Role Based Access Control Administrator on the subscription, because each stage creates its own resource group and role assignments;
- sets these variables on every GitHub Environment:

| Variable | Value |
|----------|-------|
| `AZURE_CLIENT_ID` / `AZURE_PRINCIPAL_ID` / `AZURE_TENANT_ID` | OIDC identity |
| `AZURE_SUBSCRIPTION_ID`, `AZURE_LOCATION` | Target subscription and region |
| `AZURE_ENV_NAME` | `hosted-agents-dev`, `hosted-agents-test`, `hosted-agents-prod` → resource groups `rg-hosted-agents-<stage>` |

After you run the bootstrap, open **Actions → Provision and promote hosted agent → Run workflow**. Pushes to `main` also start the pipeline.

Recommended hardening:

- Add **required reviewers** to the `test` and `prod` GitHub Environments.
- To isolate a stage in its own subscription, override `AZURE_SUBSCRIPTION_ID` on that stage's environment and grant the identity access there.
- For production, pre-create the resource groups and scope the identity's role assignments to them instead of the whole subscription.

## Security notes

- Local authentication is disabled on the Foundry resource and on Application Insights. The registry has the admin user disabled.
- The hosted agent gets only `AZURE_AI_MODEL_DEPLOYMENT_NAME`. Foundry injects the platform variables (`FOUNDRY_*`).
- The web API validates input length and ID formats, and returns generic errors so upstream details don't leak. The UI renders agent output with `textContent`, not as HTML.
- The ACA ingress is public. Before you expose `prod` to real users, enable
  [Container Apps authentication](https://learn.microsoft.com/azure/container-apps/authentication) (Entra ID) or put the app behind a gateway, so unauthenticated callers can't run up model usage.
- Customer text is untrusted input. The triage prompt treats it as data, and the reviewer removes any request for secrets.
