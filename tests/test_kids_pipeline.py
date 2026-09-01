"""Тесты детского конвейера и токен-матчера родительского контроля."""
from core.kids_pipeline import is_dangerous_for_kids
from core.parental_control import check_suspicious


def test_intercept_dangerous():
    assert is_dangerous_for_kids("выключи компьютер")
    assert is_dangerous_for_kids("открой браузер")
    assert is_dangerous_for_kids("удали папку")


def test_intercept_safe():
    assert not is_dangerous_for_kids("расскажи анекдот")
    assert not is_dangerous_for_kids("сколько будет два плюс два")
    assert not is_dangerous_for_kids("который час")


def test_suspicious_tokens_not_substrings():
    assert not check_suspicious("помоги с математикой")
    assert not check_suspicious("объясни формат даты")
    assert check_suspicious("мат")
    assert check_suspicious("ненавижу уроки")
