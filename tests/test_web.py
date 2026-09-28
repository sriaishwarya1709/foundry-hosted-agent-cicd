from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient
from openai import APIError

import app as web

SESSION = "session-1234"


@pytest.fixture
def client() -> TestClient:
    return TestClient(web.app)


@pytest.fixture
def agent() -> Mock:
    fake = Mock()
    fake.responses.create.return_value = Mock(output_text="final reply", id="resp_1")
    with patch.object(web, "agent_client", return_value=fake):
        yield fake


def test_healthz(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}


def test_index_served(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert "Contoso support desk" in response.text


def test_chat_forwards_session_to_hosted_agent(client: TestClient, agent: Mock) -> None:
    response = client.post("/api/chat", json={"message": "I was charged twice", "session_id": SESSION})

    assert response.status_code == 200
    assert response.json() == {"reply": "final reply", "response_id": "resp_1"}
    kwargs = agent.responses.create.call_args.kwargs
    assert kwargs["input"] == "I was charged twice"
    assert kwargs["extra_body"] == {"agent_session_id": SESSION}
    assert "previous_response_id" not in kwargs


def test_chat_continues_conversation(client: TestClient, agent: Mock) -> None:
    client.post("/api/chat", json={"message": "hi", "session_id": SESSION, "previous_response_id": "resp_0"})

    assert agent.responses.create.call_args.kwargs["previous_response_id"] == "resp_0"


@pytest.mark.parametrize(
    "payload",
    [
        {"message": "", "session_id": SESSION},
        {"message": "x" * 4001, "session_id": SESSION},
        {"message": "hi", "session_id": "short"},
        {"message": "hi", "session_id": "../../etc/passwd"},
        {"message": "hi", "session_id": SESSION, "previous_response_id": "bad id"},
    ],
)
def test_chat_rejects_invalid_input(client: TestClient, agent: Mock, payload: dict) -> None:
    assert client.post("/api/chat", json=payload).status_code == 422
    agent.responses.create.assert_not_called()


def test_chat_hides_upstream_errors(client: TestClient, agent: Mock) -> None:
    agent.responses.create.side_effect = APIError("secret upstream detail", request=Mock(), body=None)

    response = client.post("/api/chat", json={"message": "hi", "session_id": SESSION})

    assert response.status_code == 502
    assert "secret" not in response.text
