import logging
import os

from agent_framework.foundry import FoundryChatClient
from agent_framework_foundry_hosting import ResponsesHostServer
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

from workflow import build_workflow_agent

load_dotenv(override=False)

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("support_desk")


def main() -> None:
    model = os.environ["AZURE_AI_MODEL_DEPLOYMENT_NAME"]
    client = FoundryChatClient(
        project_endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
        model=model,
        credential=DefaultAzureCredential(),
    )
    logger.info("Starting support-desk workflow with model deployment %s", model)
    ResponsesHostServer(build_workflow_agent(client)).run()


if __name__ == "__main__":
    main()
