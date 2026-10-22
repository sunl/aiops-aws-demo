"""SigV4-signed client for the AWS DevOps Agent A2A endpoint.

Endpoint and protocol: https://docs.aws.amazon.com/devopsagent/latest/userguide/
connecting-to-devops-agent-remote-servers-integrating-with-the-devops-agent-a2a-server.html
"""
import json
import logging
import urllib.error
import urllib.parse
import urllib.request

LOG = logging.getLogger(__name__)
SIGNING_SERVICE = "aidevops"


class A2AError(RuntimeError):
    def __init__(self, status: int, body: str):
        super().__init__(f"A2A HTTP {status}: {body[:500]}")
        self.status = status
        self.body = body


def extract_text(resp: dict) -> str:
    """Join the text parts of an A2A response (task artifacts or a direct message)."""
    texts = []
    task = resp.get("task") or {}
    for artifact in task.get("artifacts") or []:
        for part in artifact.get("parts") or []:
            if part.get("text"):
                texts.append(part["text"])
    message = resp.get("message") or {}
    for part in message.get("parts") or []:
        if part.get("text"):
            texts.append(part["text"])
    return "\n\n".join(texts).strip()


def parse_investigation_started(resp: dict) -> dict:
    """Return {task_id, execution_id, a2a_task_id} from an investigation start response."""
    task = resp.get("task") or {}
    info = {"a2a_task_id": task.get("id")}
    for artifact in task.get("artifacts") or []:
        for part in artifact.get("parts") or []:
            try:
                payload = json.loads(part.get("text") or "")
            except (TypeError, ValueError):
                continue
            if isinstance(payload, dict):
                info["task_id"] = payload.get("taskId") or info.get("task_id")
                info["execution_id"] = payload.get("executionId") or info.get("execution_id")
    info.setdefault("task_id", task.get("id"))
    return info


class A2AClient:
    def __init__(self, region: str, agent_space_id: str, timeout: int = 240):
        self.base = f"https://connect.aidevops.{region}.api.aws"
        self.region = region
        self.agent_space_id = agent_space_id
        self.timeout = timeout

    def _request(self, method: str, path: str, body: dict = None, query: dict = None) -> dict:
        import boto3  # lazy imports keep the parsing helpers testable without boto3
        from botocore.auth import SigV4Auth
        from botocore.awsrequest import AWSRequest

        url = self.base + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        headers = {"A2A-Version": "1.0", "X-Agent-Space-Id": self.agent_space_id}
        if data is not None:
            headers["Content-Type"] = "application/json"
        aws_req = AWSRequest(method=method, url=url, data=data, headers=headers)
        creds = boto3.Session().get_credentials().get_frozen_credentials()
        SigV4Auth(creds, SIGNING_SERVICE, self.region).add_auth(aws_req)
        req = urllib.request.Request(url, data=data, method=method, headers=dict(aws_req.headers.items()))
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raise A2AError(exc.code, exc.read().decode("utf-8", "replace")) from exc
        return json.loads(raw) if raw else {}

    def chat(self, text: str, task_id: str = None) -> tuple:
        """Synchronous operational chat. Returns (answer_text, task_id)."""
        message = {"parts": [{"text": text}]}
        if task_id:
            message["taskId"] = task_id
        try:
            resp = self._request("POST", "/a2a/message:send", {"tenant": "chat", "message": message})
        except A2AError as exc:
            if task_id and exc.status == 400:
                LOG.warning("continuing chat %s failed (%s); starting a new chat", task_id, exc)
                return self.chat(text, None)
            raise
        task = resp.get("task") or {}
        return extract_text(resp), task.get("id")

    def start_investigation(self, text: str) -> dict:
        """Asynchronous investigation; the result arrives as an EventBridge event."""
        resp = self._request(
            "POST", "/a2a/message:send", {"tenant": "investigation", "message": {"parts": [{"text": text}]}}
        )
        return parse_investigation_started(resp)
