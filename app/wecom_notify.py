"""企业微信群「消息推送」Webhook：把告警发到群。"""
from __future__ import annotations
import httpx
from .config import load_config, httpx_trust_env


class NotifyError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def webhook_url() -> str:
    cfg = (load_config().get("alert") or {})
    return str(cfg.get("webhook") or "").strip()


def configured() -> bool:
    url = webhook_url().strip()
    if not url:
        return False
    low = url.lower()
    return low.startswith("http") and ("key=" in low or "key =" in low)


def send_text(
    content: str,
    mentioned: list | None = None,
    mentioned_mobile: list | None = None,
) -> dict:
    """发 text。mentioned 为企微 userid；mentioned_mobile 为手机号；均可空。"""
    text = str(content or "").strip()
    if not text:
        raise NotifyError("告警内容为空")
    url = webhook_url()
    if not url:
        raise NotifyError("未配置 alert.webhook")
    body = {
        "msgtype": "text",
        "text": {
            "content": text[:2048],
        },
    }
    names = [str(x).strip() for x in (mentioned or []) if str(x).strip() and str(x).strip() != "@all"]
    if names:
        body["text"]["mentioned_list"] = names[:20]
    mobiles = []
    for x in mentioned_mobile or []:
        m = str(x or "").strip()
        if m and m != "@all" and m not in mobiles:
            mobiles.append(m)
    if mobiles:
        body["text"]["mentioned_mobile_list"] = mobiles[:20]
    return _post(url, body)


def send_markdown(
    content: str,
    mentioned: list | None = None,
    mentioned_mobile: list | None = None,
) -> dict:
    """markdown 正文；需要 @ 人时改走 text（markdown_v2 不能 @）。"""
    names = [str(x).strip() for x in (mentioned or []) if str(x).strip()]
    mobiles = [str(x).strip() for x in (mentioned_mobile or []) if str(x).strip()]
    if names or mobiles:
        extra = "".join("<@" + n + ">" for n in names if n != "@all")
        text = str(content or "").strip()
        if extra:
            text = extra + "\n" + text
        return send_text(text, mentioned=names, mentioned_mobile=mobiles)
    md = str(content or "").strip()
    if not md:
        raise NotifyError("告警内容为空")
    url = webhook_url()
    if not url:
        raise NotifyError("未配置 alert.webhook")
    return _post(url, {"msgtype": "markdown", "markdown": {"content": md[:4096]}})


def _post(url: str, body: dict) -> dict:
    try:
        with httpx.Client(timeout=httpx.Timeout(8.0, connect=5.0), trust_env=httpx_trust_env()) as client:
            r = client.post(url, json=body)
    except httpx.HTTPError as e:
        raise NotifyError("企微推送网络错误：" + str(e)[:160])
    try:
        data = r.json()
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    if r.status_code >= 400 or int(data.get("errcode") or 0) != 0:
        raise NotifyError(
            "企微推送失败 HTTP "
            + str(r.status_code)
            + "："
            + str(data.get("errmsg") or data.get("error") or r.text or "")[:160]
        )
    return {"ok": True, "errcode": 0}
