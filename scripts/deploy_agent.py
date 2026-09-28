"""Build, publish, and smoke-test the Foundry hosted agent for one stage."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import (
    AgentEndpointConfig,
    ContainerConfiguration,
    FixedRatioVersionSelectionRule,
    HostedAgentDefinition,
    ProtocolVersionRecord,
    VersionSelector,
)
from azure.core.exceptions import ResourceNotFoundError
from azure.identity import DefaultAzureCredential

ROOT = Path(__file__).resolve().parents[1]
AGENT_DIR = ROOT / "src" / "agent"
IMAGE_REPOSITORY = "support-desk-agent"
FOUNDRY_USER_ROLE_ID = "53ca6127-db72-4b80-b1b0-d745d6d5456d"
HASH_EXCLUDES = {".venv", "__pycache__", ".env", ".pytest_cache"}
RETRYABLE_STATUS = {403, 404, 408, 424, 429, 500, 502, 503, 504}


@dataclass(frozen=True)
class AgentConfig:
    name: str
    description: str
    protocols: tuple[tuple[str, str], ...]
    cpu: str
    memory: str
    environment_variables: tuple[str, ...]


def load_config(path: Path = AGENT_DIR / "agent.yaml") -> AgentConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return AgentConfig(
        name=raw["name"],
        description=raw["description"],
        protocols=tuple((item["protocol"], str(item["version"])) for item in raw["protocols"]),
        cpu=str(raw["resources"]["cpu"]),
        memory=str(raw["resources"]["memory"]),
        environment_variables=tuple(raw.get("environment_variables") or ()),
    )


def source_hash(directory: Path = AGENT_DIR) -> str:
    digest = hashlib.sha256()
    for path in sorted(directory.rglob("*")):
        relative = path.relative_to(directory)
        if path.is_dir() or HASH_EXCLUDES.intersection(relative.parts):
            continue
        digest.update(relative.as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def image_tag(content_hash: str, now: datetime | None = None) -> str:
    timestamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%d%H%M%S")
    return f"{timestamp}-{content_hash[:12]}"


def require(environment: Mapping[str, str], *names: str) -> list[str]:
    missing = [name for name in names if not environment.get(name)]
    if missing:
        raise SystemExit(f"Missing required environment values: {', '.join(missing)}. Run 'azd provision' first.")
    return [environment[name] for name in names]


def agent_environment(config: AgentConfig, environment: Mapping[str, str]) -> dict[str, str]:
    return dict(zip(config.environment_variables, require(environment, *config.environment_variables)))


def build_definition(config: AgentConfig, image: str, environment_variables: dict[str, str]) -> HostedAgentDefinition:
    # No registry_connection_id: the project managed identity pulls from ACR via AcrPull.
    return HostedAgentDefinition(
        cpu=config.cpu,
        memory=config.memory,
        container_configuration=ContainerConfiguration(image=image),
        protocol_versions=[ProtocolVersionRecord(protocol=protocol, version=version) for protocol, version in config.protocols],
        environment_variables=environment_variables,
    )


def az(*args: str, capture: bool = False) -> str:
    executable = shutil.which("az")
    if not executable:
        raise SystemExit("Azure CLI ('az') is required.")
    result = subprocess.run([executable, *args], check=True, text=True, capture_output=capture)
    return result.stdout if capture else ""


def build_image(registry_name: str, image: str, context: Path = AGENT_DIR) -> None:
    print(f"Building {image} with ACR Tasks...", flush=True)
    # --no-logs still waits for the run; streamed logs crash az on non-UTF-8 Windows consoles.
    run = json.loads(
        az(
            "acr", "build",
            "--registry", registry_name,
            "--image", image,
            "--platform", "linux/amd64",
            "--no-logs",
            "--file", str(context / "Dockerfile"),
            "--output", "json",
            str(context),
            capture=True,
        )
    )
    if run.get("status") != "Succeeded":
        raise SystemExit(
            f"ACR build {run.get('runId')} finished with status {run.get('status')}. "
            f"Inspect it with: az acr task logs --registry {registry_name} --run-id {run.get('runId')}"
        )
    print(f"ACR build {run.get('runId')} succeeded.", flush=True)


def ensure_foundry_user(principal_id: str, scope: str, attempts: int = 6, delay: float = 15) -> None:
    existing = az(
        "role", "assignment", "list",
        "--assignee", principal_id,
        "--role", FOUNDRY_USER_ROLE_ID,
        "--scope", scope,
        "--query", "[].id",
        "--output", "json",
        capture=True,
    )
    if json.loads(existing or "[]"):
        return
    for attempt in range(1, attempts + 1):
        try:
            az(
                "role", "assignment", "create",
                "--assignee-object-id", principal_id,
                "--assignee-principal-type", "ServicePrincipal",
                "--role", FOUNDRY_USER_ROLE_ID,
                "--scope", scope,
                "--output", "none",
            )
            print(f"Granted Foundry User to agent identity {principal_id}")
            return
        except subprocess.CalledProcessError:
            # New agent identities can take a short time to replicate in Entra ID.
            if attempt == attempts:
                raise
            time.sleep(delay)


def latest_version(client: AIProjectClient, name: str) -> Any | None:
    try:
        return client.agents.get(name).versions.latest
    except ResourceNotFoundError:
        return None


def version_status(details: Any) -> str:
    status = getattr(details, "status", None)
    return str(getattr(status, "value", status) or "").lower()


def wait_until_active(
    client: AIProjectClient, name: str, version: str, timeout: float = 900, poll: float = 10,
    sleep: Callable[[float], None] = time.sleep,
) -> Any:
    deadline = time.monotonic() + timeout
    while True:
        details = client.agents.get_version(agent_name=name, agent_version=version)
        status = version_status(details)
        if status == "active":
            return details
        if status == "failed":
            raise SystemExit(f"Agent {name} version {version} failed to provision.")
        if time.monotonic() > deadline:
            raise SystemExit(f"Agent {name} version {version} is still '{status}' after {timeout:.0f}s.")
        print(f"Waiting for {name} version {version} (status: {status or 'unknown'})...", flush=True)
        sleep(poll)


def route_all_traffic(client: AIProjectClient, name: str, version: str) -> None:
    client.agents.update_details(
        agent_name=name,
        agent_endpoint=AgentEndpointConfig(
            version_selector=VersionSelector(
                version_selection_rules=[FixedRatioVersionSelectionRule(agent_version=version, traffic_percentage=100)]
            )
        ),
    )


def project_client(endpoint: str) -> AIProjectClient:
    return AIProjectClient(endpoint=endpoint, credential=DefaultAzureCredential(), allow_preview=True)


def deploy(environment: Mapping[str, str], config: AgentConfig, force: bool = False) -> str:
    endpoint, registry_name, login_server, account_id = require(
        environment,
        "FOUNDRY_PROJECT_ENDPOINT",
        "AZURE_CONTAINER_REGISTRY_NAME",
        "AZURE_CONTAINER_REGISTRY_ENDPOINT",
        "AZURE_AI_ACCOUNT_ID",
    )
    client = project_client(endpoint)
    content_hash = source_hash()

    current = latest_version(client, config.name)
    if (
        not force
        and current is not None
        and (current.metadata or {}).get("source_hash") == content_hash
        and version_status(current) == "active"
    ):
        print(f"{config.name} version {current.version} already runs this source; skipping build.")
        return promote(client, config.name, str(current.version), current.instance_identity, account_id)

    reference = f"{IMAGE_REPOSITORY}:{image_tag(content_hash)}"
    image = f"{login_server}/{reference}"
    build_image(registry_name, reference)

    created = client.agents.create_version(
        agent_name=config.name,
        definition=build_definition(config, image, agent_environment(config, environment)),
        description=config.description,
        metadata={
            "source_hash": content_hash,
            "git_sha": environment.get("GITHUB_SHA", "local")[:40],
            "stage": environment.get("AZURE_STAGE", ""),
        },
    )
    version = str(created.version)
    print(f"Created {config.name} version {version} from {image}")
    wait_until_active(client, config.name, version)
    return promote(client, config.name, version, created.instance_identity, account_id)


def promote(client: AIProjectClient, name: str, version: str, identity: Any, account_id: str) -> str:
    # Role grant and traffic routing are idempotent, so they also run when the build is skipped.
    identity = identity or client.agents.get(name).instance_identity
    if identity and identity.principal_id:
        ensure_foundry_user(identity.principal_id, account_id)
    route_all_traffic(client, name, version)
    print(f"Promoted {name} version {version}")
    return version


def invoke(endpoint: str, agent_name: str, prompt: str, attempts: int = 8, delay: float = 20) -> str:
    from openai import APIStatusError

    client = project_client(endpoint).get_openai_client(agent_name=agent_name)
    session_id = f"smoke-{uuid.uuid4().hex}"
    for attempt in range(1, attempts + 1):
        try:
            response = client.responses.create(input=prompt, extra_body={"agent_session_id": session_id})
        except APIStatusError as error:
            # Covers session warm-up (424), RBAC propagation (403), and throttling (429).
            if error.status_code not in RETRYABLE_STATUS or attempt == attempts:
                raise
            print(f"Invoke attempt {attempt} returned {error.status_code}; retrying in {delay:.0f}s...", flush=True)
            time.sleep(delay)
            continue
        if not response.output_text:
            raise SystemExit(f"Agent {agent_name} returned an empty response.")
        return response.output_text
    raise AssertionError("unreachable")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    deploy_parser = commands.add_parser("deploy", help="Build the image and create a new hosted agent version.")
    deploy_parser.add_argument("--force", action="store_true", help="Create a new version even if the source is unchanged.")
    invoke_parser = commands.add_parser("invoke", help="Send a smoke-test prompt to the hosted agent.")
    invoke_parser.add_argument("--prompt", default="I was charged twice for my subscription this month. Can I get a refund?")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    config = load_config()
    if args.command == "deploy":
        deploy(os.environ, config, force=args.force)
    else:
        [endpoint] = require(os.environ, "FOUNDRY_PROJECT_ENDPOINT")
        print(invoke(endpoint, os.environ.get("AZURE_AI_AGENT_NAME", config.name), args.prompt))


if __name__ == "__main__":
    sys.exit(main())
