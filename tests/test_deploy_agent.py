import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import yaml
from azure.ai.projects.models import AgentVersionStatus

from scripts import deploy_agent
from scripts.deploy_agent import (
    agent_environment,
    build_definition,
    deploy,
    image_tag,
    load_config,
    source_hash,
    wait_until_active,
)

ROOT = Path(__file__).resolve().parents[1]
ENVIRONMENT = {
    "FOUNDRY_PROJECT_ENDPOINT": "https://aif-x.services.ai.azure.com/api/projects/proj-dev",
    "AZURE_CONTAINER_REGISTRY_NAME": "crx",
    "AZURE_CONTAINER_REGISTRY_ENDPOINT": "crx.azurecr.io",
    "AZURE_AI_ACCOUNT_ID": "/subscriptions/s/resourceGroups/rg/providers/Microsoft.CognitiveServices/accounts/aif-x",
    "AZURE_AI_MODEL_DEPLOYMENT_NAME": "gpt-4.1-mini",
    "AZURE_STAGE": "dev",
}


def test_agent_yaml_matches_bicep_default_agent_name() -> None:
    config = load_config()
    bicep = (ROOT / "infra" / "main.bicep").read_text(encoding="utf-8")

    assert f"param agentName string = '{config.name}'" in bicep
    assert config.protocols == (("responses", "2.0.0"),)


def test_agent_yaml_does_not_set_platform_reserved_variables() -> None:
    for name in load_config().environment_variables:
        assert not name.startswith(("FOUNDRY_", "AGENT_"))
        assert name not in {"PORT", "HOME", "APPLICATIONINSIGHTS_CONNECTION_STRING", "OTEL_EXPORTER_OTLP_ENDPOINT"}


def test_parameters_file_is_valid_json() -> None:
    parameters = json.loads((ROOT / "infra" / "main.parameters.json").read_text(encoding="utf-8"))["parameters"]

    assert parameters["environmentName"]["value"] == "${AZURE_ENV_NAME}"
    assert parameters["webExists"]["value"] == "${SERVICE_WEB_RESOURCE_EXISTS=false}"


def test_source_hash_ignores_local_artifacts(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("print('hi')", encoding="utf-8")
    baseline = source_hash(tmp_path)
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "main.pyc").write_bytes(b"x")
    (tmp_path / ".env").write_text("SECRET=1", encoding="utf-8")

    assert source_hash(tmp_path) == baseline

    (tmp_path / "main.py").write_text("print('changed')", encoding="utf-8")
    assert source_hash(tmp_path) != baseline


def test_image_tag_is_timestamped_and_content_addressed() -> None:
    tag = image_tag("abcdef0123456789", datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc))

    assert tag == "20260102030405-abcdef012345"


def test_agent_environment_requires_values() -> None:
    config = load_config()

    assert agent_environment(config, ENVIRONMENT) == {"AZURE_AI_MODEL_DEPLOYMENT_NAME": "gpt-4.1-mini"}
    with pytest.raises(SystemExit, match="AZURE_AI_MODEL_DEPLOYMENT_NAME"):
        agent_environment(config, {})


def test_build_definition_is_hosted_container() -> None:
    definition = build_definition(load_config(), "crx.azurecr.io/a:1", {"A": "B"})

    body = definition.as_dict()
    assert body["kind"] == "hosted"
    assert body["container_configuration"] == {"image": "crx.azurecr.io/a:1"}
    assert body["protocol_versions"] == [{"protocol": "responses", "version": "2.0.0"}]
    assert body["environment_variables"] == {"A": "B"}


def test_wait_until_active_fails_fast_on_failed_version() -> None:
    client = Mock()
    client.agents.get_version.return_value = Mock(status="failed")

    with pytest.raises(SystemExit, match="failed"):
        wait_until_active(client, "agent", "3", sleep=lambda _: None)


def test_wait_until_active_polls_until_active() -> None:
    client = Mock()
    client.agents.get_version.side_effect = [
        Mock(status=AgentVersionStatus.CREATING),
        Mock(status=AgentVersionStatus.ACTIVE),
    ]

    wait_until_active(client, "agent", "3", sleep=lambda _: None)

    assert client.agents.get_version.call_count == 2


@patch.object(deploy_agent, "ensure_foundry_user")
@patch.object(deploy_agent, "build_image")
@patch.object(deploy_agent, "project_client")
def test_deploy_skips_unchanged_source_but_still_promotes(
    project_client: Mock, build_image: Mock, ensure_role: Mock
) -> None:
    latest = Mock(
        version="4",
        status=AgentVersionStatus.ACTIVE,
        metadata={"source_hash": source_hash()},
        instance_identity=Mock(principal_id="agent-oid"),
    )
    project_client.return_value.agents.get.return_value.versions.latest = latest

    assert deploy(ENVIRONMENT, load_config()) == "4"
    build_image.assert_not_called()
    project_client.return_value.agents.create_version.assert_not_called()
    ensure_role.assert_called_once_with("agent-oid", ENVIRONMENT["AZURE_AI_ACCOUNT_ID"])
    project_client.return_value.agents.update_details.assert_called_once()


@patch.object(deploy_agent, "ensure_foundry_user")
@patch.object(deploy_agent, "wait_until_active")
@patch.object(deploy_agent, "build_image")
@patch.object(deploy_agent, "project_client")
def test_deploy_creates_version_grants_identity_and_routes_traffic(
    project_client: Mock, build_image: Mock, wait: Mock, ensure_role: Mock
) -> None:
    client = project_client.return_value
    client.agents.get.return_value.versions.latest = Mock(version="1", status="active", metadata={"source_hash": "old"})
    client.agents.create_version.return_value = Mock(version="2", instance_identity=Mock(principal_id="agent-oid"))

    assert deploy(ENVIRONMENT, load_config()) == "2"

    reference = build_image.call_args.args[1]
    assert reference.startswith("support-desk-agent:")
    kwargs = client.agents.create_version.call_args.kwargs
    assert kwargs["definition"].container_configuration.image == f"crx.azurecr.io/{reference}"
    assert kwargs["metadata"]["source_hash"] == source_hash()
    ensure_role.assert_called_once_with("agent-oid", ENVIRONMENT["AZURE_AI_ACCOUNT_ID"])
    wait.assert_called_once()
    rules = client.agents.update_details.call_args.kwargs["agent_endpoint"].version_selector.version_selection_rules
    assert rules[0].agent_version == "2"
    assert rules[0].traffic_percentage == 100


def test_workflow_stages_match_github_environments() -> None:
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8"))
    stages = [job["with"]["stage"] for name, job in workflow["jobs"].items() if name.startswith("deploy-")]

    assert stages == ["dev", "test", "prod"]
