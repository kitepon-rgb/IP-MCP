
from ip_mcp.jpo.status_codes import JpoApiError, JpoOutcome, parse_envelope


def _envelope(status: str, *, data: dict | None = None, message: str = "", remain: str = "100"):
    return {
        "result": {
            "statusCode": status,
            "errorMessage": message,
            "remainAccessCount": remain,
            "data": data or {},
        }
    }


class TestParseEnvelope:
    def test_success(self):
        env = parse_envelope(_envelope("100", data={"applicationNumber": "2017204947"}))
        assert env.outcome is JpoOutcome.OK
        assert env.is_ok
        assert env.data["applicationNumber"] == "2017204947"
        assert env.remain_access_count == "100"

    def test_not_found(self):
        env = parse_envelope(_envelope("107", message="該当データなし"))
        assert env.outcome is JpoOutcome.NOT_FOUND
        assert not env.is_ok
        assert "該当データなし" in env.error_message

    def test_daily_quota(self):
        env = parse_envelope(_envelope("203", message="1日のアクセス上限を超過しました"))
        assert env.outcome is JpoOutcome.DAILY_QUOTA_EXCEEDED

    def test_invalid_token(self):
        env = parse_envelope(_envelope("210", message="無効なトークンです"))
        assert env.outcome is JpoOutcome.INVALID_TOKEN

    def test_server_busy_is_retryable(self):
        env = parse_envelope(_envelope("303"))
        assert env.outcome is JpoOutcome.SERVER_BUSY
        assert env.is_retryable

    def test_unknown_code_falls_through(self):
        env = parse_envelope(_envelope("777"))
        assert env.outcome is JpoOutcome.UNKNOWN_ERROR

    def test_missing_result_object(self):
        env = parse_envelope({})
        assert env.outcome is JpoOutcome.UNKNOWN_ERROR
        assert env.status_code == ""

    def test_empty_remain_count_becomes_none(self):
        env = parse_envelope(_envelope("100", remain=""))
        assert env.remain_access_count is None


class TestJpoApiError:
    def test_message_includes_endpoint(self):
        env = parse_envelope(_envelope("203", message="quota"))
        err = JpoApiError(env, endpoint="/api/patent/v1/app_progress/2017204947")
        assert "daily_quota_exceeded" in str(err)
        assert "203" in str(err)
        assert "/api/patent/v1/app_progress" in str(err)
