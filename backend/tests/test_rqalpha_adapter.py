from app.adapters.rqalpha.adapter import RQAlphaValidationAdapter
from app.adapters.rqalpha.result_converter import compare_engine_results


def test_rqalpha_unavailable_state_is_non_fatal() -> None:
    status = RQAlphaValidationAdapter().status()

    assert status["engine_type"] == "rqalpha"
    assert isinstance(status["available"], bool)
    if not status["available"]:
        assert "rqalpha-validation" in status["installation_hint"]


def test_rqalpha_difference_report_explains_trade_and_fee_gaps() -> None:
    report = compare_engine_results(
        self_result={
            "performance": {"tradable_return": 0.10, "total_fees": 100.0},
            "trades": [{"symbol": "A"}, {"symbol": "B"}],
            "execution_failures": [{"symbol": "C", "reason": "limit_up_unbuyable"}],
        },
        rqalpha_result={
            "performance": {"total_return": 0.12, "total_fees": 80.0},
            "trades": [{"symbol": "A"}],
        },
    )

    assert report["return_difference"] == -0.02
    assert report["trade_count_difference"] == 1
    assert report["fee_difference"] == 20.0
    assert report["unmatched_trades"] == ["B"]
    assert report["execution_difference"][0]["reason"] == "limit_up_unbuyable"
