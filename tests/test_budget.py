# tests/test_budget.py
from concurrent.futures import ThreadPoolExecutor

import pytest

from core.budget import Budget


def test_charge_accumulates_and_exceeded_boundary():
    b = Budget(limit=100)
    assert b.spent == 0 and not b.exceeded
    b.charge(60, 30)                      # spent 90 < 100
    assert b.spent == 90 and not b.exceeded
    b.charge(5, 5)                        # spent 100 == limit → exceeded(>= 语义)
    assert b.spent == 100 and b.exceeded


def test_charge_none_usage_tolerated():
    b = Budget(limit=10)
    b.charge(None, None)                  # 客户端 usage 缺字段时不崩
    assert b.spent == 0
    b.charge(5, None)                     # 单边缺字段(生产真实形态)
    assert b.spent == 5


def test_concurrent_charge_thread_safe():
    b = Budget(limit=10**9)
    def work(_):
        for _ in range(1000):
            b.charge(1, 0)
    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(work, range(8)))
    assert b.spent == 8000                 # Lock 下无丢失更新


def test_limit_must_be_positive():
    with pytest.raises(ValueError, match="limit"):
        Budget(limit=0)
    with pytest.raises(ValueError, match="limit"):
        Budget(limit=-5)
