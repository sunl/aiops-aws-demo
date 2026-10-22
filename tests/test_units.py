"""Unit tests for the demo Lambdas. Run: .venv/bin/python -m unittest discover -s tests -v"""
import base64
import hashlib
import hmac
import importlib.util
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "common"))

from demo_common import a2a, feishu  # noqa: E402


def load(name: str):
    spec = importlib.util.spec_from_file_location(f"{name}_app", ROOT / "src" / name / "app.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


forwarder = load("alert_forwarder")
nexus = load("nexus_mcp")
_spec = importlib.util.spec_from_file_location("es_tools", ROOT / "src" / "es_mcp" / "es_tools.py")
es_tools = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(es_tools)
notifier = load("notifier")
worker = load("chat_worker")
_spec = importlib.util.spec_from_file_location("feishu_ws_client", ROOT / "src" / "feishu_ws" / "client.py")
ws = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ws)

ALARM = {
    "AlarmName": "aiops-demo-order-service-errors",
    "AlarmDescription": "[P1] order-service 订单服务调用失败数异常（每分钟 >= 5 次）",
    "AWSAccountId": "111122223333",
    "NewStateValue": "ALARM",
    "NewStateReason": "Threshold Crossed: 1 datapoint [20.0] was >= the threshold (5.0).",
    "StateChangeTime": "2026-10-14T10:21:00.000+0000",
    "Region": "US East (N. Virginia)",
    "AlarmArn": "arn:aws:cloudwatch:us-east-1:111122223333:alarm:aiops-demo-order-service-errors",
    "Trigger": {"MetricName": "Errors", "Namespace": "AWS/Lambda", "Statistic": "SUM", "Period": 60,
                "ComparisonOperator": "GreaterThanOrEqualToThreshold", "Threshold": 5.0,
                "Dimensions": [{"value": "aiops-demo-order-service", "name": "FunctionName"}]},
}

SUMMARY_MD = """# Investigation Summary

## Symptoms

### order-service 调用失败数飙升
**Description:** 10:20 起每分钟 20 次失败。

## Findings

### Cause: order-service 1.4.0 将 ORDERS_TABLE 指向不存在的表 aiops-demo-orders-v2
**Description:** CloudTrail 显示 10:19 UpdateFunctionConfiguration；Nexus 发布说明要求先执行迁移任务。

### Cause: 迁移任务 orders-v2-migration 未执行
"""


class FeishuTests(unittest.TestCase):
    def test_bot_sign_is_hmac_of_empty_message(self):
        expected = base64.b64encode(hmac.new(b"1700000000\nsec", b"", hashlib.sha256).digest()).decode()
        self.assertEqual(feishu.bot_sign(1700000000, "sec"), expected)

    def test_md_headings_become_bold(self):
        out = feishu.md_to_lark_md("# Title\n\n\n\n## Sub **x**\n- item")
        self.assertEqual(out, "**Title**\n\n**Sub **x**\n- item")
        self.assertNotIn("#", out)

    def test_truncate_adds_note_at_line_boundary(self):
        text = "\n".join(f"line {i}" for i in range(2000))
        out = feishu.truncate(text, 500)
        self.assertLessEqual(len(out), 500 + len(feishu.TRUNCATION_NOTE))
        self.assertTrue(out.endswith(feishu.TRUNCATION_NOTE))
        self.assertEqual(feishu.truncate("short", 500), "short")

    def test_build_card_shape(self):
        card = feishu.build_card("t", "red", ["a", "", "b"], note="n", button=("go", "https://x"))
        tags = [e["tag"] for e in card["elements"]]
        self.assertEqual(tags, ["div", "hr", "div", "action", "note"])
        self.assertEqual(card["header"]["template"], "red")
        card = feishu.build_card("t", "red", ["a"], button=("go", ""))
        self.assertNotIn("action", [e["tag"] for e in card["elements"]])


class ForwarderTests(unittest.TestCase):
    def test_signature_matches_documented_openssl_recipe(self):
        ts, body, secret = "2026-10-14T10:21:05.000Z", '{"title":"订单"}', "s3cr3t"
        ours = forwarder.sign(secret, ts, body)
        manual = base64.b64encode(hmac.new(secret.encode(), f"{ts}:{body}".encode(), hashlib.sha256).digest()).decode()
        self.assertEqual(ours, manual)
        if shutil.which("openssl"):
            proc = subprocess.run(
                ["bash", "-c", 'printf "%s" "$1:$2" | openssl dgst -sha256 -hmac "$3" -binary | base64', "_", ts, body, secret],
                capture_output=True, text=True, check=True)
            self.assertEqual(ours, proc.stdout.strip())

    def test_timestamp_format(self):
        self.assertEqual(forwarder.webhook_timestamp(0), "1970-01-01T00:00:00.000Z")

    def test_build_incident_created(self):
        p = forwarder.build_incident(ALARM, "created", "inc-1", "order-service", "demo")
        self.assertEqual((p["eventType"], p["incidentId"], p["action"], p["priority"]), ("incident", "inc-1", "created", "HIGH"))
        self.assertIn("aiops-demo-order-service-errors", p["description"])
        self.assertIn("Threshold Crossed", p["description"])
        self.assertIn("FunctionName=aiops-demo-order-service", p["description"])
        self.assertTrue(p["title"].startswith("[order-service]"))

    def test_build_incident_resolved_and_priorities(self):
        p = forwarder.build_incident({**ALARM, "NewStateValue": "OK"}, "resolved", "inc-1", "order-service", "demo")
        self.assertEqual(p["action"], "resolved")
        self.assertIn("已恢复", p["description"])
        self.assertEqual(forwarder.priority_from("[P0] x"), "CRITICAL")
        self.assertEqual(forwarder.priority_from("no tag"), "HIGH")

    def test_process_alarm_collapses_repeats_and_resolves(self):
        from botocore.exceptions import ClientError

        class FakeTable:
            def __init__(self):
                self.items = {}

            def put_item(self, Item, **kw):
                if Item["pk"] in self.items:
                    raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "PutItem")
                self.items[Item["pk"]] = Item

            def get_item(self, Key):
                return {"Item": self.items.get(Key["pk"])} if Key["pk"] in self.items else {}

            def delete_item(self, Key):
                self.items.pop(Key["pk"], None)

        table, sent = FakeTable(), []
        env = {"WEBHOOK_SECRET_ID": "w", "FEISHU_SECRET_ID": "f", "STATE_TABLE": "t"}
        with mock.patch.dict("os.environ", env), \
                mock.patch.object(forwarder, "_table", return_value=table), \
                mock.patch.object(forwarder, "get_secret_json", return_value={"url": "https://hook", "secret": "s"}), \
                mock.patch.object(forwarder, "post_webhook", side_effect=lambda u, s, p: sent.append(p)), \
                mock.patch.object(forwarder, "_notify_feishu"):
            first = forwarder.process_alarm(ALARM)
            second = forwarder.process_alarm(ALARM)
            resolved = forwarder.process_alarm({**ALARM, "NewStateValue": "OK"})
        self.assertEqual(first["action"], "created")
        self.assertTrue(second["suppressed"])
        self.assertEqual(resolved["incident_id"], first["incident_id"])
        self.assertEqual([p["action"] for p in sent], ["created", "resolved"])


class NexusMcpTests(unittest.TestCase):
    state = staticmethod(lambda: {"deployed_version": "1.4.0", "deployed_at": "2026-10-14T10:19:00Z", "deployed_by": "release-bot"})

    def rpc(self, method, params=None, req_id=1):
        return nexus.handle_rpc({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}}, self.state)

    def test_initialize_negotiates_version(self):
        self.assertEqual(self.rpc("initialize", {"protocolVersion": "2025-03-26"})["result"]["protocolVersion"], "2025-03-26")
        res = self.rpc("initialize", {"protocolVersion": "1999-01-01"})["result"]
        self.assertEqual(res["protocolVersion"], nexus.DEFAULT_VERSION)
        self.assertIn("tools", res["capabilities"])

    def test_tools_list_names_fit_devops_agent_limit(self):
        tools = self.rpc("tools/list")["result"]["tools"]
        self.assertEqual({t["name"] for t in tools}, {"nexus_list_versions", "nexus_get_release"})
        self.assertTrue(all(len(t["name"]) <= 64 for t in tools))

    def test_list_versions_marks_deployed(self):
        res = self.rpc("tools/call", {"name": "nexus_list_versions", "arguments": {"component": "order-service"}})["result"]
        data = json.loads(res["content"][0]["text"])
        deployed = [v for v in data["versions"] if v["status"] == "deployed-production"]
        self.assertEqual([v["version"] for v in deployed], ["1.4.0"])
        self.assertEqual(deployed[0]["deployed_at"], "2026-10-14T10:19:00Z")

    def test_get_release_contains_root_cause_hints(self):
        res = self.rpc("tools/call", {"name": "nexus_get_release", "arguments": {"component": "order-service", "version": "1.4.0"}})["result"]
        text = res["content"][0]["text"]
        self.assertFalse(res["isError"])
        self.assertIn("aiops-demo-orders-v2", text)
        self.assertIn("orders-v2-migration", text)

    def test_errors(self):
        res = self.rpc("tools/call", {"name": "nexus_get_release", "arguments": {"component": "nope", "version": "1"}})["result"]
        self.assertTrue(res["isError"])
        self.assertEqual(self.rpc("tools/call", {"name": "bogus"})["error"]["code"], -32602)
        self.assertEqual(self.rpc("resources/list")["error"]["code"], -32601)
        self.assertIsNone(nexus.handle_rpc({"jsonrpc": "2.0", "method": "notifications/initialized"}, self.state))

    def test_http_layer(self):
        ev = lambda method, body: {"requestContext": {"http": {"method": method}}, "body": body}
        self.assertEqual(nexus.handler(ev("GET", ""), None)["statusCode"], 405)
        self.assertEqual(nexus.handler(ev("POST", "{not json"), None)["statusCode"], 400)
        self.assertEqual(nexus.handler(ev("POST", '{"jsonrpc":"2.0","method":"notifications/initialized"}'), None)["statusCode"], 202)
        resp = nexus.handler(ev("POST", '{"jsonrpc":"2.0","id":7,"method":"ping"}'), None)
        self.assertEqual((resp["statusCode"], json.loads(resp["body"])["id"]), (200, 7))
        self.assertEqual(resp["headers"]["Content-Type"], "application/json")


class FakeLogs:
    """Logs Insights stand-in: each started query returns `statuses` in turn, then its rows."""

    def __init__(self, rows_by_query, statuses=("Running", "Complete")):
        self.rows_by_query, self.statuses = rows_by_query, statuses
        self.started, self.stopped, self.polls = [], [], {}

    def start_query(self, **kw):
        self.started.append(kw)
        return {"queryId": f"q{len(self.started)}"}

    def get_query_results(self, queryId):
        n = self.polls.get(queryId, 0)
        self.polls[queryId] = n + 1
        status = self.statuses[min(n, len(self.statuses) - 1)]
        query = self.started[int(queryId[1:]) - 1]["queryString"]
        rows = next((r for key, r in self.rows_by_query.items() if key in query), [])
        return {"status": status, "results": rows if status == "Complete" else []}

    def stop_query(self, queryId):
        self.stopped.append(queryId)


def row(**fields):
    return [{"field": k, "value": v} for k, v in fields.items()] + [{"field": "@ptr", "value": "x"}]


class EsMcpToolTests(unittest.TestCase):
    groups = {"order-service": "/aws/lambda/aiops-demo-order-service"}
    now = 1791973260  # 2026-10-14T10:21:00Z

    def test_build_search_query(self):
        q = es_tools.build_search_query("ERROR", "ResourceNotFound", 5)
        self.assertEqual(q, 'fields @timestamp, @message | filter ispresent(level) | filter level = "ERROR" | '
                            'filter @message like "ResourceNotFound" | sort @timestamp desc | limit 5')
        self.assertNotIn("like", es_tools.build_search_query("", "", 20))
        self.assertIn('like "订单 失败"', es_tools.build_search_query("", "订单 失败", 20))

    def test_rejects_unsafe_or_out_of_range_input(self):
        for level, text, size in [("DEBUG", "", 5), ("", 'x" | stats count(*)', 5), ("", "a|b", 5),
                                  ("", "x" * 101, 5), ("", "", 0), ("", "", 101)]:
            with self.assertRaises(es_tools.ToolError):
                es_tools.build_search_query(level, text, size)
        with self.assertRaisesRegex(es_tools.ToolError, "logs-order-service-"):
            es_tools.search_logs(FakeLogs({}), self.groups, "payment-service", now=self.now)
        with self.assertRaises(es_tools.ToolError):
            es_tools.error_summary(FakeLogs({}), self.groups, "order-service", minutes=0, now=self.now)
        with self.assertRaises(ValueError):
            es_tools.parse_log_groups("[1, 2]")
        self.assertEqual(es_tools.parse_log_groups(""), {})

    def test_search_logs_es_shape(self):
        msg = json.dumps({"level": "ERROR", "service": "order-service", "version": "1.4.0",
                          "table": "aiops-demo-orders-v2", "error_type": "ResourceNotFoundException"})
        logs = FakeLogs({"sort @timestamp": [row(**{"@timestamp": "2026-10-14 10:20:05.123", "@message": msg + "\n"}),
                                             row(**{"@timestamp": "2026-10-14 10:20:04.000", "@message": "plain text\n"})]})
        out = es_tools.search_logs(logs, self.groups, "order-service", level="ERROR", minutes=30, size=2,
                                   now=self.now, sleep=lambda s: None)
        started = logs.started[0]
        self.assertEqual((started["logGroupName"], started["endTime"] - started["startTime"]),
                         ("/aws/lambda/aiops-demo-order-service", 1800))
        self.assertIn('level = "ERROR"', started["queryString"])
        first, second = out["hits"]["hits"]
        self.assertEqual(first["_index"], "logs-order-service-2026.10.14")
        self.assertEqual(first["_source"]["@timestamp"], "2026-10-14T10:20:05.123Z")
        self.assertEqual(first["_source"]["table"], "aiops-demo-orders-v2")
        self.assertEqual(second["_source"]["message"], "plain text")
        self.assertEqual(out["hits"]["total"], {"value": 2, "relation": "gte"})

    def test_error_summary_aggregations(self):
        logs = FakeLogs({
            "by error_type": [row(error_type="ResourceNotFoundException", version="1.4.0", table="aiops-demo-orders-v2",
                                  doc_count="38", first_seen="2026-10-14 10:19:42.000", last_seen="2026-10-14 10:20:59.000")],
            "bin(1m)": [row(minute="2026-10-14 10:19:00.000", doc_count="18"), row(minute="2026-10-14 10:20:00.000", doc_count="20")],
        })
        out = es_tools.error_summary(logs, self.groups, "order-service", minutes=60, now=self.now, sleep=lambda s: None)
        self.assertEqual(len(logs.started), 2)
        bucket = out["aggregations"]["by_error"]["buckets"][0]
        self.assertEqual((bucket["key"], bucket["doc_count"], bucket["version"]), ("ResourceNotFoundException", 38, "1.4.0"))
        self.assertEqual(bucket["first_seen"], "2026-10-14T10:19:42.000Z")
        self.assertEqual([b["doc_count"] for b in out["aggregations"]["errors_per_minute"]["buckets"]], [18, 20])
        self.assertEqual(out["hits"]["total"]["value"], 38)

    def test_query_failure_and_timeout(self):
        with self.assertRaisesRegex(es_tools.ToolError, "failed"):
            es_tools.run_queries(FakeLogs({}, statuses=("Failed",)), "g", 0, 1, ["q"], sleep=lambda s: None)
        ticks = iter(range(0, 1000, 10))
        logs = FakeLogs({}, statuses=("Running",))
        with self.assertRaisesRegex(es_tools.ToolError, "did not finish"):
            es_tools.run_queries(logs, "g", 0, 1, ["q"], sleep=lambda s: None, clock=lambda: next(ticks))
        self.assertEqual(logs.stopped, ["q1"])


class NotifierTests(unittest.TestCase):
    def test_extract_causes(self):
        causes = notifier.extract_causes(SUMMARY_MD)
        self.assertEqual(len(causes), 2)
        self.assertIn("aiops-demo-orders-v2", causes[0])

    def test_summary_text_variants(self):
        self.assertEqual(notifier.summary_text("md"), "md")
        self.assertEqual(notifier.summary_text({"markdown": "md"}), "md")

    def _event(self, detail_type, **data):
        return {"metadata": {"agent_space_id": "s", "task_id": "t1", "execution_id": "e1"}, "data": data}

    def test_completed_builds_rca_card(self):
        with mock.patch.object(notifier, "fetch_task", return_value={"title": "[order-service] alarm", "priority": "HIGH"}), \
                mock.patch.object(notifier, "fetch_summary", return_value=SUMMARY_MD) as fs:
            card = notifier.build_card_for("Investigation Completed", self._event("x", summary_record_id="r1"), "https://app")
        fs.assert_called_once_with("s", "e1", "r1", "investigation_summary_md")
        blob = json.dumps(card, ensure_ascii=False)
        self.assertIn("根因结论", blob)
        self.assertIn("https://app", blob)
        self.assertEqual(card["header"]["template"], "green")

    def test_other_event_types(self):
        with mock.patch.object(notifier, "fetch_task", return_value={}), \
                mock.patch.object(notifier, "fetch_summary", return_value=""):
            created = notifier.build_card_for("Investigation Created", self._event("x"), "")
            empty = notifier.build_card_for("Investigation Completed", self._event("x"), "")
            linked = notifier.build_card_for("Investigation Linked", self._event("x"), "")
            unknown = notifier.build_card_for("Investigation In Progress", self._event("x"), "")
        self.assertEqual(created["header"]["template"], "blue")
        self.assertIn("未生成摘要", json.dumps(empty, ensure_ascii=False))
        self.assertIn("关联", linked["header"]["title"]["content"])
        self.assertIsNone(unknown)


class PostToGroupTests(unittest.TestCase):
    CARD = {"header": {}, "elements": []}
    APP = {"app_id": "cli_x", "app_secret": "s", "group_webhook_url": "https://hook", "group_webhook_secret": ""}

    def test_app_bot_when_chat_id_and_app_credentials(self):
        with mock.patch.object(feishu.FeishuApp, "send_card") as send, mock.patch.object(feishu, "send_group_card") as hook:
            self.assertEqual(feishu.post_to_group(self.APP, self.CARD, "https://open.feishu.cn", "oc_1"), "app")
        send.assert_called_once_with("oc_1", self.CARD)
        hook.assert_not_called()

    def test_webhook_fallback_and_dropped(self):
        with mock.patch.object(feishu, "send_group_card") as hook:
            self.assertEqual(feishu.post_to_group(self.APP, self.CARD, "https://open.feishu.cn", ""), "webhook")
            no_app = {**self.APP, "app_id": ""}
            self.assertEqual(feishu.post_to_group(no_app, self.CARD, "https://open.feishu.cn", "oc_1"), "webhook")
        self.assertEqual(hook.call_count, 2)
        self.assertEqual(feishu.post_to_group({}, self.CARD, "https://open.feishu.cn", ""), "dropped")

    def test_send_card_request(self):
        app = feishu.FeishuApp("https://open.feishu.cn/", "cli_x", "s")
        app._token, app._token_expiry = "t-1", float("inf")
        with mock.patch.object(feishu, "_post_json", return_value={"code": 0}) as post:
            app.send_card("oc_1", self.CARD)
        url, body = post.call_args.args
        self.assertEqual(url, "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id")
        self.assertEqual((body["receive_id"], body["msg_type"]), ("oc_1", "interactive"))
        self.assertEqual(json.loads(body["content"]), self.CARD)
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer t-1")


class FeishuWsTests(unittest.TestCase):
    MSG = {"schema": "2.0", "header": {"event_id": "ev1", "event_type": "im.message.receive_v1"},
           "event": {"sender": {"sender_type": "user", "sender_id": {"open_id": "ou_1"}},
                     "message": {"message_id": "om_1", "chat_id": "oc_1", "chat_type": "group",
                                 "message_type": "text", "content": json.dumps({"text": "@_user_1 hi"})}}}

    def test_route(self):
        self.assertEqual(ws.route(self.MSG, ""), ("message", "om_1"))
        # dedup key is message_id, so a re-push with a new event_id is still caught
        repush = {**self.MSG, "header": {**self.MSG["header"], "event_id": "ev2"}}
        self.assertEqual(ws.route(repush, ""), ("message", "om_1"))
        self.assertEqual(ws.route(self.MSG, "oc_2")[0], "ignore")
        self.assertEqual(ws.route(self.MSG, "oc_2, oc_1"), ("message", "om_1"))
        for sender_type in ("app", "bot"):
            bot = {**self.MSG, "event": {**self.MSG["event"], "sender": {"sender_type": sender_type}}}
            self.assertEqual(ws.route(bot, "")[0], "ignore")
        self.assertEqual(ws.route({"event": {"message": {}}}, ""), ("ignore", "no message_id"))

    def test_worker_payload_keeps_message_fields_only(self):
        out = ws.worker_payload(self.MSG)
        self.assertEqual(set(out), {"event"})
        self.assertNotIn("sender", out["event"])
        self.assertEqual(out["event"]["message"]["message_id"], "om_1")
        # the worker must be able to parse what the client forwards
        self.assertEqual(worker.message_text(out["event"]["message"]), "hi")

    def _dispatcher(self, put_side_effect=None):
        table, lam = mock.Mock(), mock.Mock()
        table.put_item.side_effect = put_side_effect
        return ws.Dispatcher(table, lam, "aiops-demo-chat-worker"), table, lam

    def test_dispatch_accepts_and_forwards_async(self):
        dispatch, table, lam = self._dispatcher()
        self.assertEqual(dispatch(self.MSG), "accepted")
        self.assertEqual(table.put_item.call_args.kwargs["Item"]["pk"], "feishu-msg#om_1")
        kwargs = lam.invoke.call_args.kwargs
        self.assertEqual((kwargs["FunctionName"], kwargs["InvocationType"]), ("aiops-demo-chat-worker", "Event"))
        self.assertEqual(json.loads(kwargs["Payload"])["event"]["message"]["chat_id"], "oc_1")

    def test_dispatch_drops_duplicates_and_reraises_other_errors(self):
        class Conflict(Exception):
            response = {"Error": {"Code": "ConditionalCheckFailedException"}}

        dispatch, _, lam = self._dispatcher(Conflict())
        self.assertEqual(dispatch(self.MSG), "duplicate")
        lam.invoke.assert_not_called()
        dispatch, _, _ = self._dispatcher(RuntimeError("throttled"))
        with self.assertRaises(RuntimeError):
            dispatch(self.MSG)

    def test_wait_for_credentials(self):
        reads = iter([{}, RuntimeError("throttled"), {"app_id": "cli_x", "app_secret": ""},
                      {"app_id": "cli_x", "app_secret": "s"}])

        def read():
            value = next(reads)
            if isinstance(value, Exception):
                raise value
            return value

        sleeps = []
        cfg = ws.wait_for_credentials(read, sleep=sleeps.append, interval=7)
        self.assertEqual(cfg["app_secret"], "s")
        self.assertEqual(sleeps, [7, 7, 7])  # empty, error and half-filled secrets are all waited out

    def test_redact_ws_url(self):
        rec = __import__("logging").LogRecord("Lark", 20, __file__, 1,
                                               "connected to %s [conn_id=1]", ("wss://h.example/ws/v2?ticket=SECRET&x=1",), None)
        self.assertTrue(ws.RedactWsUrl().filter(rec))
        self.assertEqual(rec.getMessage(), "connected to wss://h.example/ws/v2?<redacted> [conn_id=1]")
        plain = __import__("logging").LogRecord("x", 20, __file__, 1, "no url %s", ("here",), None)
        ws.RedactWsUrl().filter(plain)
        self.assertEqual(plain.getMessage(), "no url here")

    def test_dispatch_ignores_without_side_effects(self):
        dispatch, table, lam = self._dispatcher()
        dispatch.allowed_chats = "oc_other"
        self.assertEqual(dispatch(self.MSG), "ignored")
        table.put_item.assert_not_called()
        lam.invoke.assert_not_called()


class ChatWorkerTests(unittest.TestCase):
    def test_message_text_strips_mentions(self):
        m = {"message_type": "text", "content": json.dumps({"text": "@_user_1  order-service 最近发布了什么？"})}
        self.assertEqual(worker.message_text(m), "order-service 最近发布了什么？")
        self.assertEqual(worker.message_text({"message_type": "image", "content": "{}"}), "")

    def test_parse_command(self):
        self.assertEqual(worker.parse_command("调查 order-service 报错"), ("investigate", "order-service 报错"))
        self.assertEqual(worker.parse_command("/investigate: 503"), ("investigate", "503"))
        self.assertEqual(worker.parse_command("调查"), ("help", ""))
        self.assertEqual(worker.parse_command(""), ("help", ""))
        self.assertEqual(worker.parse_command("有哪些告警"), ("chat", "有哪些告警"))


class A2ATests(unittest.TestCase):
    def test_extract_text(self):
        resp = {"task": {"id": "t", "artifacts": [{"parts": [{"text": "a"}, {"text": "b"}]}]}}
        self.assertEqual(a2a.extract_text(resp), "a\n\nb")
        self.assertEqual(a2a.extract_text({"message": {"parts": [{"text": "m"}]}}), "m")

    def test_parse_investigation_started(self):
        resp = {"task": {"id": "a2a-1", "status": {"state": "TASK_STATE_WORKING"}, "artifacts": [
            {"parts": [{"text": json.dumps({"type": "investigation_started", "taskId": "task-9", "executionId": "exe-9"})}]}]}}
        self.assertEqual(a2a.parse_investigation_started(resp), {"a2a_task_id": "a2a-1", "task_id": "task-9", "execution_id": "exe-9"})
        self.assertEqual(a2a.parse_investigation_started({"task": {"id": "x"}})["task_id"], "x")

    def test_chat_retries_without_stale_task_id(self):
        client = a2a.A2AClient("us-east-1", "space")
        calls = []

        def fake(method, path, body=None, query=None):
            calls.append(body)
            if body["message"].get("taskId"):
                raise a2a.A2AError(400, "task not found")
            return {"task": {"id": "new", "artifacts": [{"parts": [{"text": "ok"}]}]}}

        with mock.patch.object(client, "_request", side_effect=fake):
            self.assertEqual(client.chat("hi", "old"), ("ok", "new"))
        self.assertEqual([c["message"].get("taskId") for c in calls], ["old", None])
        self.assertEqual(calls[0]["tenant"], "chat")


class Boto3ContractTests(unittest.TestCase):
    """The pinned boto3 must expose the DevOps Agent operations the Lambdas call."""

    def test_devops_agent_operations_exist(self):
        import boto3

        client = boto3.client("devops-agent", region_name="us-east-1")
        self.assertTrue(client.can_paginate("list_journal_records"))
        self.assertTrue(hasattr(client, "get_backlog_task"))
        model = client.meta.service_model
        self.assertIn("recordType", model.operation_model("ListJournalRecords").input_shape.members)
        self.assertIn("taskId", model.operation_model("GetBacklogTask").input_shape.members)


if __name__ == "__main__":
    unittest.main()
