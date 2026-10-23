#!/usr/bin/env python3
"""Generate aws-architecture.svg: the demo deployment drawn with AWS Architecture Icons.

The icons are embedded as <symbol>s, so the SVG is self-contained and can be edited
by hand afterwards. To regenerate it, download the official icon package
(https://aws.amazon.com/architecture/icons/ -> "Icon package"), unzip it, then run
from the project root:

    python3 docs/diagrams/aws_architecture.py --icons <unzipped package dir>
    docs/diagrams/export_png.sh aws-architecture.svg
"""
import argparse
import html
import re
from pathlib import Path

OUT = Path(__file__).resolve().parent / "aws-architecture.svg"
W, H = 1800, 1250

# symbol id -> file name inside the package (searched recursively)
ICONS = {
    "cloud": "AWS-Cloud-logo_32.svg",
    "region": "Region_32.svg",
    "account": "AWS-Account_32.svg",
    "vpc": "Virtual-private-cloud-VPC_32.svg",
    "subnet": "Public-subnet_32.svg",
    "lambda": "Arch_AWS-Lambda_64.svg",
    "ddb": "Arch_Amazon-DynamoDB_64.svg",
    "sns": "Arch_Amazon-Simple-Notification-Service_64.svg",
    "eventbridge": "Arch_Amazon-EventBridge_64.svg",
    "cloudwatch": "Arch_Amazon-CloudWatch_64.svg",
    "alarm": "Res_Amazon-CloudWatch_Alarm_48.svg",
    "logs": "Res_Amazon-CloudWatch_Logs_48.svg",
    "param": "Res_AWS-Systems-Manager_Parameter-Store_48.svg",
    "fargate": "Arch_AWS-Fargate_64.svg",
    "ecr": "Arch_Amazon-Elastic-Container-Registry_64.svg",
    "secrets": "Arch_AWS-Secrets-Manager_64.svg",
    "s3": "Arch_Amazon-Simple-Storage-Service_64.svg",
    "cognito": "Arch_Amazon-Cognito_64.svg",
    "iam": "Arch_AWS-Identity-and-Access-Management_64.svg",
    "cloudtrail": "Arch_AWS-CloudTrail_64.svg",
    "cfn": "Arch_AWS-CloudFormation_64.svg",
    "devopsagent": "Arch_AWS-DevOps-Agent_64.svg",
    "agentcore": "Arch_Amazon-Bedrock-AgentCore_64.svg",
    "user": "Res_User_48_Light.svg",
    "users": "Res_Users_48_Light.svg",
}

STYLE = """
.bg{fill:#FFFFFF}
.h{fill:#232F3E;font-size:20px;font-weight:700}
.gl{fill:#232F3E;font-size:14px;font-weight:700}
.gn{fill:#545B64;font-size:12px}
.il{fill:#232F3E;font-size:12px;font-weight:700}
.i2{fill:#545B64;font-size:11px}
.al{fill:#232F3E;font-size:11.5px;paint-order:stroke;stroke:#FFFFFF;stroke-width:4px;stroke-linejoin:round}
.it{fill:#232F3E;font-size:12.5px;font-weight:700}
.is{fill:#545B64;font-size:11px}
.ft{fill:#545B64;font-size:12px}
.cloud{fill:none;stroke:#232F3E;stroke-width:1.5}
.region{fill:none;stroke:#00A4A6;stroke-width:1.5;stroke-dasharray:6 4}
.account{fill:none;stroke:#E7157B;stroke-width:1.5}
.vpc{fill:none;stroke:#8C4FFF;stroke-width:1.5}
.subnet{fill:#F2F6E8;stroke:#7AA116;stroke-width:1.2}
.grp{fill:none;stroke:#7D8998;stroke-width:1.2;stroke-dasharray:5 4}
.ext{fill:#EEF4FF;stroke:#3370FF;stroke-width:1.4}
.item{fill:#FFFFFF;stroke:#B8C0CC;stroke-width:1}
.ln{fill:none;stroke:#545B64;stroke-width:1.6}
.rd{fill:none;stroke:#545B64;stroke-width:1.4;stroke-dasharray:5 4}
.mk{fill:#545B64}
"""


def esc(text: str) -> str:
    return html.escape(text, quote=False)


def num(m):
    return f"{float(m.group(0)):.2f}".rstrip("0").rstrip(".")


def symbol(sym_id: str, path: Path) -> str:
    src = path.read_text(encoding="utf-8")
    view_box = re.search(r'viewBox="([^"]+)"', src).group(1)
    body = re.search(r"<svg[^>]*>(.*)</svg>", src, re.S).group(1)
    body = re.sub(r"<title>.*?</title>", "", body, flags=re.S)
    body = re.sub(r'\s(id|xlink:href)="[^"]*"', "", body)          # ids repeat across icons
    body = re.sub(r"-?\d+\.\d{3,}", num, body)                    # 2 decimals is plenty here
    body = re.sub(r">\s+<", "><", body).strip()
    return f'<symbol id="i-{sym_id}" viewBox="{view_box}">{body}</symbol>'


def find_icons(root: Path) -> dict:
    found = {}
    for sym_id, name in ICONS.items():
        hits = [p for p in root.rglob(name) if "__MACOSX" not in p.parts]
        if not hits:
            raise SystemExit(f"icon not found in package: {name}")
        found[sym_id] = hits[0]
    return found


class Svg:
    def __init__(self):
        self.parts = []

    def add(self, s):
        self.parts.append(s)

    def icon(self, name, x, y, size):
        self.add(f'<use href="#i-{name}" x="{x}" y="{y}" width="{size}" height="{size}"/>')

    def text(self, x, y, s, cls, anchor="start"):
        a = "" if anchor == "start" else f' text-anchor="{anchor}"'
        self.add(f'<text x="{x}" y="{y}" class="{cls}"{a}>{esc(s)}</text>')

    def svc(self, name, cx, cy, l1, l2="", size=48):
        """Service icon centred on (cx, cy), two label lines under it."""
        self.icon(name, cx - size / 2, cy - size / 2, size)
        self.text(cx, cy + size / 2 + 16, l1, "il", "middle")
        if l2:
            self.text(cx, cy + size / 2 + 31, l2, "i2", "middle")

    def svc_side(self, name, cx, cy, l1, l2, size=40):
        """Service icon with its labels to the right (bottom row)."""
        self.icon(name, cx - size / 2, cy - size / 2, size)
        self.text(cx + size / 2 + 8, cy - 3, l1, "il")
        self.text(cx + size / 2 + 8, cy + 13, l2, "i2")

    def group(self, cls, x, y, w, h, label, icon=None, icon_size=28, note=None):
        self.add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" class="{cls}"/>')
        if icon:
            self.icon(icon, x, y, icon_size)
            self.text(x + icon_size + 8, y + icon_size / 2 + 5, label, "gl")
        else:
            self.text(x + 16, y + 22, label, "gl")
        if note:
            self.text(x + 16, y + 40, note, "gn")

    def item(self, x, y, w, h, title, sub):
        self.add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" class="item"/>')
        self.text(x + 12, y + h / 2 - 3, title, "it")
        self.text(x + 12, y + h / 2 + 14, sub, "is")

    def arrow(self, d, read=False, both=False, label=None, lx=0, ly=0, anchor="middle"):
        start = ' marker-start="url(#a)"' if both else ""
        self.add(f'<path d="{d}" class="{"rd" if read else "ln"}" marker-end="url(#a)"{start}/>')
        if label:
            self.text(lx, ly, label, "al", anchor)


def build(icon_root: Path) -> str:
    syms = "".join(symbol(k, p) for k, p in find_icons(icon_root).items())
    s = Svg()
    s.add(f'<rect width="{W}" height="{H}" class="bg"/>')
    s.text(20, 42, "AWS 架构图：AIOps Demo（CloudFormation 栈 aiops-demo，us-east-1）", "h")

    # ---------------------------------------------------------------- outside AWS
    s.svc("user", 115, 270, "运维人员", "演示 / 部署脚本")
    s.add('<rect x="20" y="420" width="190" height="520" rx="10" class="ext"/>')
    s.icon("users", 91, 440, 48)
    s.text(115, 512, "飞书开放平台", "gl", "middle")
    for i, line in enumerate(["企业自建应用（应用机器人）", "", "演示群", "· 告警 / 恢复卡片", "· 开始调查 / 根因卡片",
                              "· @机器人 问答（话题内多轮）", "", "事件经长连接推送", "无公网回调地址"]):
        s.text(36, 540 + i * 20, line, "i2")

    # ---------------------------------------------------------------- AWS groups
    s.group("cloud", 240, 70, 1540, 1138, "AWS Cloud", icon="cloud")
    s.group("region", 258, 108, 1504, 1004, "us-east-1 · 美国东部（弗吉尼亚北部）", icon="region", icon_size=26)
    s.group("account", 276, 146, 1468, 950, "AWS 账号（运维账号）", icon="account", icon_size=26)

    # ① 模拟业务
    s.group("grp", 296, 176, 720, 170, "模拟业务")
    s.svc("eventbridge", 380, 270, "EventBridge 规则", "每分钟触发")
    s.svc("lambda", 520, 270, "traffic", "20 次 / 分钟")
    s.svc("lambda", 660, 270, "order-service", "故障注入对象")
    s.svc("ddb", 800, 270, "DynamoDB", "aiops-demo-orders")
    s.arrow("M404,270 H494")
    s.arrow("M544,270 H634")
    s.arrow("M684,270 H774")
    s.arrow("M139,270 H218 V222 H660 V244", label="inject-fault.sh / rollback.sh：修改函数配置",
            lx=430, ly=216, anchor="start")

    # ② 告警接入
    s.group("grp", 296, 366, 720, 180, "告警接入")
    s.text(312, 412, "ALARM / OK 都发到 SNS", "gn")
    s.text(312, 430, "同一告警 15 分钟只建一个事件", "gn")
    s.svc("alarm", 660, 456, "CloudWatch 告警", "Errors ≥ 5 / 分钟")
    s.svc("sns", 800, 456, "SNS", "aiops-demo-alerts")
    s.svc("lambda", 940, 456, "alert-forwarder", "去重 · HMAC 签名")
    s.arrow("M660,330 V430", label="Errors 指标", lx=668, ly=361, anchor="start")
    s.arrow("M684,456 H774")
    s.arrow("M824,456 H914")
    s.arrow("M964,456 H1060", label="HMAC", lx=1012, ly=448)
    s.arrow("M940,518 V532 H212", label="告警 / 恢复卡片", lx=560, ly=528)

    # ③ 飞书集成: card rows on top, the Fargate task at the bottom so its pull lines can run
    # straight down to S3 and ECR Public without crossing the lines that go to Feishu.
    s.group("grp", 296, 566, 720, 400, "飞书集成")
    s.svc("eventbridge", 940, 640, "EventBridge", "aws.aidevops 事件")
    s.svc("lambda", 760, 640, "feishu-notifier", "调查事件 → 卡片")
    s.arrow("M1060,640 H966", label="调查事件", lx=1012, ly=632)
    s.arrow("M916,640 H786")
    s.arrow("M736,640 H212", label="开始调查 / 根因卡片", lx=460, ly=632)
    s.group("vpc", 316, 744, 290, 186, "默认 VPC", icon="vpc", icon_size=24)
    s.group("subnet", 332, 776, 258, 140, "公有子网", icon="subnet", icon_size=22)
    s.text(470, 814, "ECS Fargate · feishu-ws", "il", "middle")
    s.text(470, 829, "安全组：无入站 · 出站 443", "i2", "middle")
    s.icon("fargate", 446, 842, 48)
    s.svc("lambda", 760, 866, "chat-worker", "问答 Worker")
    s.arrow("M446,866 H212", both=True)
    s.text(392, 858, "WSS 长连接", "al", "middle")
    s.text(392, 884, "客户端主动出站", "al", "middle")
    s.arrow("M494,866 H734", label="异步调用", lx=670, ly=858)
    s.arrow("M784,866 H1060", label="A2A · SigV4", lx=920, ly=858)
    s.arrow("M760,842 V725 H212", label="话题内回复卡片", lx=660, ly=717)
    # every task start: the fetch container copies the code bundle from S3, both images come from ECR Public
    s.arrow("M454,890 V1138", read=True)
    s.arrow("M486,890 V1018", read=True)
    s.text(446, 952, "启动时拉取镜像", "al", "end")
    s.text(494, 952, "启动时拉取代码包", "al", "start")

    # AWS DevOps Agent
    s.group("grp", 1046, 176, 280, 790, "")
    s.icon("devopsagent", 1062, 192, 48)
    s.text(1120, 208, "AWS DevOps Agent", "gl")
    s.text(1120, 226, "Agent Space aiops-demo", "gn")
    s.text(1120, 242, "控制台创建，不在栈内", "i2")
    s.item(1062, 262, 248, 46, "Web App", "调查过程 · 对话")
    s.item(1062, 322, 248, 46, "Skill", "order-service-release-rca")
    s.item(1062, 430, 248, 52, "Webhook（HMAC）", "创建调查 · 标记 resolved")
    s.item(1062, 500, 248, 46, "自主调查（只读）", "Triage → RCA → 处置建议")
    s.item(1062, 614, 248, 52, "调查事件 → EventBridge", "Created · Completed · Failed")
    s.item(1062, 840, 248, 52, "A2A 端点（SigV4）", "chat · investigation")
    s.item(1062, 906, 248, 46, "原生数据源（只读角色）", "CloudWatch · CloudTrail")

    # 自定义 MCP Server
    s.group("grp", 1356, 176, 368, 790, "自定义 MCP Server")
    s.svc("agentcore", 1450, 280, "es-mock MCP", "AgentCore Runtime")
    s.svc("cognito", 1640, 280, "Amazon Cognito", "OAuth 令牌")
    s.svc("logs", 1450, 420, "CloudWatch Logs", "订单服务日志")
    s.svc("lambda", 1450, 580, "nexus-mock MCP", "函数 URL · AWS_IAM")
    s.svc("param", 1640, 580, "SSM 参数", "发布版本（脚本更新）")
    s.arrow("M1326,280 H1424", label="MCP · OAuth", lx=1376, ly=272)
    s.arrow("M1326,580 H1424", label="MCP · SigV4", lx=1376, ly=572)
    s.arrow("M1474,280 H1614", read=True, both=True, label="JWT 授权", lx=1545, ly=272)
    s.arrow("M1450,340 V394", read=True, label="Logs Insights", lx=1458, ly=372, anchor="start")
    s.arrow("M1474,580 H1614", read=True, label="读取", lx=1545, ly=572)
    s.add('<rect x="1372" y="680" width="336" height="112" rx="6" class="item"/>')
    for i, (line, cls) in enumerate([("4 个工具，均标记为 READ_ONLY", "it"),
                                     ("es_error_summary · es_search_logs", "is"),
                                     ("nexus_list_versions · nexus_get_release", "is"),
                                     ("查询参数按白名单校验（防注入）", "is")]):
        s.text(1386, 704 + i * 22, line, cls)

    # 共享资源
    s.group("grp", 296, 986, 1428, 96, "共享资源")
    s.svc_side("s3", 486, 1040, "S3 制品桶", "代码包（Fargate 启动时读取）")
    s.svc_side("ddb", 720, 1040, "DynamoDB 状态表", "去重 · 会话 · 调查映射")
    s.svc_side("secrets", 912, 1040, "Secrets Manager", "Webhook 密钥 · 飞书凭证")
    s.svc_side("cloudwatch", 1110, 1040, "CloudWatch", "指标与日志")
    s.svc_side("cloudtrail", 1270, 1040, "CloudTrail", "API 变更记录")
    s.svc_side("iam", 1420, 1040, "IAM 角色", "最小权限")
    s.svc_side("cfn", 1550, 1040, "CloudFormation", "栈 aiops-demo")
    s.arrow("M1110,952 V1018", read=True)
    s.arrow("M1270,952 V1018", read=True)
    s.text(1190, 1004, "只读", "al", "middle")

    # AWS-managed public registry: outside the account, so it sits in the AWS Cloud below the region
    s.svc_side("ecr", 454, 1160, "Amazon ECR Public", "aws-cli 与 python 镜像（按 digest 锁定）")
    s.text(482, 1189, "AWS 托管的公共镜像仓库，不在本账号内", "i2")

    s.text(20, 1236, "实线箭头为调用方向，虚线为只读访问或授权校验。所有入口都要认证（HMAC、SigV4、OAuth、飞书应用凭证）；"
                     "飞书事件经 Fargate 出站长连接接收，栈内没有公网回调地址。"
                     "Fargate 任务经公网 IP 出站访问飞书、S3 与 ECR Public。图标：AWS Architecture Icons。", "ft")

    head = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
            "font-family=\"'Noto Sans CJK SC','Noto Sans SC','PingFang SC','Microsoft YaHei',sans-serif\">\n"
            f"<!-- Generated by aws_architecture.py; icons: AWS Architecture Icons (embedded as symbols). -->\n"
            f"<style>{STYLE}</style>\n"
            '<defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
            'orient="auto-start-reverse"><path d="M0,0L10,5L0,10z" class="mk"/></marker>\n'
            f"{syms}</defs>\n")
    return head + "\n".join(s.parts) + "\n</svg>\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--icons", required=True, type=Path, help="unzipped AWS Architecture Icons package")
    args = ap.parse_args()
    OUT.write_text(build(args.icons), encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
