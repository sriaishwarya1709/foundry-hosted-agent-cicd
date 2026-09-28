"""Contoso support desk web front-end that calls the Foundry hosted agent."""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from openai import APIError, OpenAI
from pydantic import BaseModel, Field

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("support_desk.web")

STATIC_DIR = Path(__file__).resolve().parent / "static"
ID_PATTERN = r"^[A-Za-z0-9_-]{8,128}$"


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    session_id: str = Field(pattern=ID_PATTERN)
    previous_response_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,256}$")


class ChatReply(BaseModel):
    reply: str
    response_id: str


@lru_cache(maxsize=1)
def credential() -> DefaultAzureCredential:
    # AZURE_CLIENT_ID selects the container app's user-assigned managed identity.
    return DefaultAzureCredential()


@lru_cache(maxsize=1)
def agent_client() -> OpenAI:
    project = AIProjectClient(
        endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
        credential=credential(),
        allow_preview=True,
    )
    return project.get_openai_client(agent_name=os.environ["AZURE_AI_AGENT_NAME"])


def configure_telemetry() -> None:
    if os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING"):
        from azure.monitor.opentelemetry import configure_azure_monitor

        configure_azure_monitor(credential=credential())


configure_telemetry()
app = FastAPI(title="Contoso support desk", docs_url=None, redoc_url=None)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/info")
def info() -> dict[str, str]:
    return {"agent": os.getenv("AZURE_AI_AGENT_NAME", ""), "stage": os.getenv("APP_STAGE", "")}


@app.post("/api/chat", response_model=ChatReply)
def chat(request: ChatRequest) -> ChatReply:
    options = {"previous_response_id": request.previous_response_id} if request.previous_response_id else {}
    try:
        response = agent_client().responses.create(
            input=request.message,
            extra_body={"agent_session_id": request.session_id},
            **options,
        )
    except APIError:
        logger.exception("Hosted agent call failed for session %s", request.session_id)
        raise HTTPException(status_code=502, detail="The support agent is unavailable. Please try again.")
    return ChatReply(reply=response.output_text or "", response_id=response.id)
