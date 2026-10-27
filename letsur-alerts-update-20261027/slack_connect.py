"""Read local credentials privately; authenticate and optionally send one smoke test."""
import argparse
import json
import re
import sqlite3
import urllib.error
import urllib.request
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from delivery import DefiniteFailure


class OutcomeUnknown(Exception):
    pass


def load_settings(path):
    values = {}
    allowed = {"SLACK_BOT_TOKEN", "SLACK_TEAM_ID", "SLACK_CHANNEL_ID", "SLACK_SIGNING_SECRET", "SLACK_APP_TOKEN"}
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or key not in allowed or key in values:
            raise ValueError("설정 파일의 항목·중복을 확인하세요. 값은 출력하지 않았습니다.")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    token = values.get("SLACK_BOT_TOKEN", "")
    if not token.startswith("xoxb-") or any(c.isspace() for c in token):
        raise ValueError("Bot User OAuth Token이 비어 있거나 형식이 올바르지 않습니다.")
    for key, prefix in (("SLACK_TEAM_ID", "T"), ("SLACK_CHANNEL_ID", "C")):
        if not re.fullmatch(prefix + "[A-Z0-9]+", values.get(key, "")):
            raise ValueError(key + " 형식을 확인하세요.")
    return values


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class SlackClient:
    def __init__(self, token, opener=None):
        self._token = token
        self._opener = opener or urllib.request.build_opener(NoRedirect())

    def call(self, method, payload):
        if method not in {"auth.test", "chat.postMessage", "chat.update"}:
            raise ValueError("지원하지 않는 Slack API")
        request = urllib.request.Request(
            "https://slack.com/api/" + method,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": "Bearer " + self._token,
                     "Content-Type": "application/json; charset=utf-8"}, method="POST")
        try:
            with self._opener.open(request, timeout=20) as stream:
                body = stream.read(1024 * 1024)
        except urllib.error.HTTPError as error:
            if error.code == 429:
                try:
                    wait = min(3600, max(1, int(error.headers.get("Retry-After", "60"))))
                except ValueError:
                    wait = 60
                raise DefiniteFailure("rate_limited", retryable=True, retry_after_seconds=wait) from None
            if 400 <= error.code < 500:
                raise DefiniteFailure("http_" + str(error.code)) from None
            raise OutcomeUnknown("Slack 응답 결과를 확인할 수 없습니다") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise OutcomeUnknown("네트워크 연결 또는 전송 결과를 확인할 수 없습니다") from None
        try:
            response = json.loads(body)
        except (ValueError, UnicodeError):
            raise OutcomeUnknown("Slack 응답을 해석할 수 없습니다") from None
        if not isinstance(response, dict):
            raise OutcomeUnknown("Slack 응답 형식을 확인할 수 없습니다")
        if response.get("ok") is not True:
            code = response.get("error", "unknown_error")
            if not isinstance(code, str) or not re.fullmatch("[a-z_]{1,80}", code):
                code = "unknown_error"
            raise DefiniteFailure(code)
        return response

    def authenticate(self, expected_team):
        response = self.call("auth.test", {})
        if response.get("team_id") != expected_team or not response.get("bot_id"):
            raise ValueError("예상한 워크스페이스의 봇이 아닙니다. 발송하지 않았습니다.")
        return {"team_id": response["team_id"], "team": response.get("team"),
                "user_id": response.get("user_id"), "bot_id": response["bot_id"]}

    def send(self, payload, job_id):
        response = self.call("chat.postMessage", payload)
        return {"channel": response.get("channel"), "ts": response.get("ts")}


def smoke_test(client, settings, receipt_path):
    """Single authorized smoke message; repeat calls cannot create a second message."""
    identity = client.authenticate(settings["SLACK_TEAM_ID"])
    receipt = Path(receipt_path)
    receipt.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(receipt)) as db, db:
        db.execute("""CREATE TABLE IF NOT EXISTS smoke_tests (
          team TEXT NOT NULL, channel TEXT NOT NULL, state TEXT NOT NULL,
          ts TEXT, attempted_at TEXT NOT NULL, PRIMARY KEY(team,channel))""")
        team, channel = settings["SLACK_TEAM_ID"], settings["SLACK_CHANNEL_ID"]
        db.execute("BEGIN IMMEDIATE")
        old = db.execute("SELECT state,ts FROM smoke_tests WHERE team=? AND channel=?", (team, channel)).fetchone()
        if old and old[0] == "sent":
            return {"status": "already_sent", "identity": identity, "channel": channel, "ts": old[1]}
        if old and old[0] in ("sending", "unknown"):
            raise OutcomeUnknown("이전 테스트 발송 결과 대조가 필요합니다. 중복 발송을 중단했습니다.")
        db.execute("INSERT OR REPLACE INTO smoke_tests VALUES(?,?,'sending',NULL,?)",
                   (team, channel, datetime.now(timezone.utc).isoformat()))
        db.commit()
        text = ("LETSUR 알림 봇 연결 테스트\n"
                "테스트 워크스페이스 인증과 채널 발송이 정상 동작합니다.\n"
                "실제 시장 사건 알림이 아닌 연결 확인 메시지입니다.\n"
                "확인 버튼·자동 재알림은 다음 연결 단계에서 검증합니다.")
        try:
            result = client.send({"channel": channel, "text": text,
                                  "unfurl_links": False, "unfurl_media": False}, "connection-test")
            if result.get("channel") != channel or not isinstance(result.get("ts"), str):
                raise OutcomeUnknown("메시지 응답을 대조할 수 없습니다")
        except DefiniteFailure:
            db.execute("UPDATE smoke_tests SET state='failed' WHERE team=? AND channel=?", (team, channel))
            db.commit()
            raise
        except Exception:
            db.execute("UPDATE smoke_tests SET state='unknown' WHERE team=? AND channel=?", (team, channel))
            db.commit()
            raise OutcomeUnknown("테스트 메시지 전송 결과가 불명확합니다. 자동 재발송하지 않습니다.") from None
        db.execute("UPDATE smoke_tests SET state='sent',ts=? WHERE team=? AND channel=?", (result["ts"], team, channel))
        return {"status": "sent", "identity": identity, **result}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=str(Path(__file__).with_name(".env")))
    parser.add_argument("--send-test", action="store_true")
    parser.add_argument("--receipt", default=str(Path(__file__).parent / "output" / "slack-connection.sqlite"))
    args = parser.parse_args()
    try:
        settings = load_settings(args.env_file)
        client = SlackClient(settings["SLACK_BOT_TOKEN"])
        if args.send_test:
            result = smoke_test(client, settings, args.receipt)
        else:
            result = {"status": "authenticated", "identity": client.authenticate(settings["SLACK_TEAM_ID"])}
        print(json.dumps(result, ensure_ascii=True))
    except (ValueError, DefiniteFailure, OutcomeUnknown, OSError) as error:
        # Only controlled error messages are exposed. Never print token/header/body/traceback.
        if isinstance(error, OSError):
            message = "로컬 설정 또는 기록 파일에 접근할 수 없습니다"
        else:
            message = str(error)
        print(json.dumps({"status": "failed", "reason": message}, ensure_ascii=True))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
