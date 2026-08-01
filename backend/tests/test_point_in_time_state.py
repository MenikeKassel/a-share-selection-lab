import pandas as pd
from app.data.history import join_historical_state


def test_future_industry_classification_does_not_overwrite_history() -> None:
    daily = pd.DataFrame(
        [
            {"date": "2025-12-31", "symbol": "A", "close": 10.0},
            {"date": "2026-01-02", "symbol": "A", "close": 11.0},
        ]
    )
    history = pd.DataFrame(
        [
            {
                "symbol": "A",
                "effective_date": "2020-01-01",
                "industry": "旧行业",
                "is_st": False,
            },
            {
                "symbol": "A",
                "effective_date": "2026-01-01",
                "industry": "新行业",
                "is_st": True,
            },
        ]
    )

    result = join_historical_state(daily, history)

    old = result.loc[result["date"] == pd.Timestamp("2025-12-31")].iloc[0]
    new = result.loc[result["date"] == pd.Timestamp("2026-01-02")].iloc[0]
    assert old["industry"] == "旧行业"
    assert bool(old["is_st"]) is False
    assert new["industry"] == "新行业"
    assert bool(new["is_st"]) is True
