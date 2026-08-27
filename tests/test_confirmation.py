"""
Тесты Confirmation Manager v3.

Запуск без pytest:  python tests/test_confirmation.py
Совместимы с pytest:  python -m pytest tests/ -v

ID: C1,C2 — корректность; H1-H3 — hazards; M1,M2 — matcher;
    P1 — prompting lifecycle; X1 — fail-safe; A1,A2 — regression
    для конкурентных сценариев.
"""
import sys
import time
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.confirmation import ConfirmationManager


def _ok(payload):
    return "done"


def _prompt(m, a):
    """Полный lifecycle доставки промпта: claim + mark."""
    assert m.claim_for_prompt(a.id)
    assert m.mark_prompted(a.id)


# ------------------------------------------------------------------ C1
def test_c1_single_pending():
    m = ConfirmationManager()
    a = m.create_pending("t", "A", {}, _ok)
    # unclaimed pending заменяем (liveness)
    b = m.create_pending("t", "B", {}, _ok)
    assert b is not None and a.status == "discarded"
    # PROMPTING блокирует замену
    assert m.claim_for_prompt(b.id)
    assert m.create_pending("t", "C", {}, _ok) is None
    # PROMPTED блокирует замену
    assert m.mark_prompted(b.id)
    assert m.create_pending("t", "D", {}, _ok) is None
    assert m.get_active().id == b.id


# ------------------------------------------------------------------ C2
def test_c2_confirm_atomic_single_execution():
    m = ConfirmationManager()
    runs = []

    def executor(payload):
        runs.append(1)
        time.sleep(0.05)
        return "executed"

    a = m.create_pending("t", "X", {}, executor)
    _prompt(m, a)

    results = []
    res_lock = threading.Lock()

    def worker():
        ok, _ = m.confirm(a.id, source="voice")
        with res_lock:
            results.append(ok)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)
    assert all(not t.is_alive() for t in threads)
    assert results.count(True) == 1
    assert len(runs) == 1
    # повторное «да» — no-op
    ok, _ = m.confirm(a.id, source="voice")
    assert ok is False and len(runs) == 1


# ------------------------------------------------------------------ H1
def test_h1_executor_runs_outside_lock():
    m = ConfirmationManager()
    seen = {}

    def executor(payload):
        seen["has_pending"] = m.has_pending()  # deadlock, если lock удержан
        return "ok"

    a = m.create_pending("t", "X", {}, executor)
    _prompt(m, a)
    t = threading.Thread(
        target=lambda: seen.update(result=m.confirm(a.id, source="voice"))
    )
    t.start()
    t.join(timeout=3)
    assert not t.is_alive(), "deadlock: executor выполнен под lock"
    assert seen["has_pending"] is False
    assert seen["result"][0] is True


# ------------------------------------------------------------------ H2
def test_h2_only_voice_or_user_can_confirm():
    for bad in ("llm", "tool", "model", "text", "", None):
        m = ConfirmationManager()
        a = m.create_pending("t", "X", {}, _ok)
        _prompt(m, a)
        ok, _ = m.confirm(a.id, source=bad)
        assert ok is False
        assert m.get_active() is not None

    for good in ("voice", "user"):
        m = ConfirmationManager()
        a = m.create_pending("t", "X", {}, _ok)
        _prompt(m, a)
        ok, _ = m.confirm(a.id, source=good)
        assert ok is True


# ------------------------------------------------------------------ H3
def test_h3_ttl_starts_at_prompt():
    m = ConfirmationManager()
    a = m.create_pending("t", "X", {}, _ok, ttl_seconds=0.2)
    time.sleep(0.3)                 # дольше TTL, но промпт не доставлен
    assert not a.is_expired()       # TTL не стартовал
    _prompt(m, a)
    ok, _ = m.confirm(a.id, source="voice")
    assert ok is True               # окно считалось от озвучки

    m2 = ConfirmationManager()
    b = m2.create_pending("t", "Y", {}, _ok, ttl_seconds=0.2)
    _prompt(m2, b)
    time.sleep(0.3)
    ok, _ = m2.confirm(b.id, source="voice")
    assert ok is False              # истёк
    assert m2.get_active() is None  # удалён атомарно


# ------------------------------------------------------------------ M1
def test_m1_ambiguous_never_confirms():
    m = ConfirmationManager()
    runs = []
    a = m.create_pending("t", "X", {}, lambda p: runs.append(1) or "ok")
    _prompt(m, a)

    decision, act = m.match_user_input("да нет")
    assert decision == "ambiguous" and act.id == a.id
    assert m.get_active() is not None and not runs

    decision, act = m.match_user_input("да")
    assert decision == "confirm"
    ok, _ = m.confirm(act.id, source="voice")
    assert ok is True and runs


# ------------------------------------------------------------------ M2
def test_m2_strict_matcher():
    m = ConfirmationManager()
    a = m.create_pending("t", "X", {}, _ok)
    _prompt(m, a)

    ambiguous_cases = [
        "да нет",                      # смешанные токены
        "да, выключай компьютер",      # подтверждение + контекст
        "конечно нет",                 # согласие + негация
        "давай потом",                 # согласие + отсрочка
        "ага, но позже",               # согласие + условие
        "выключи что-нибудь да",       # команда + хвост
        "сколько будет два плюс два",  # неизвестная длинная фраза
        "подтверждаю что угодно кроме этого",  # длинный контекст
    ]
    for phrase in ambiguous_cases:
        decision, act = m.match_user_input(phrase)
        assert decision == "ambiguous", f"'{phrase}' не ambiguous"
        assert act.id == a.id

    # pending не тронут и не выполнен
    assert m.get_active() is not None

    # чистые фразы работают
    assert m.match_user_input("да")[0] == "confirm"
    assert m.match_user_input("подтверждаю")[0] == "confirm"
    assert m.match_user_input("нет")[0] == "reject"
    assert m.match_user_input("отмени")[0] == "reject"
    assert m.match_user_input("не надо")[0] == "reject"


# ------------------------------------------------------------------ P1
def test_p1_prompting_lifecycle_and_abort():
    m = ConfirmationManager()
    a = m.create_pending("t", "X", {}, _ok)
    assert a.status == "pending"
    assert m.claim_for_prompt(a.id)
    assert a.status == "prompting"
    assert not m.claim_for_prompt(a.id)      # повторный claim — нет

    # Ошибка TTS: явная отмена PROMPTING освобождает слот
    assert m.abort_prompting(a.id)
    assert a.status == "aborted"
    assert m.get_active() is None
    assert not m.mark_prompted(a.id)         # aborted не озвучивается

    new = m.create_pending("t", "Y", {}, _ok)   # следующий разрешён
    assert new is not None

    # счастливый путь после abort
    assert m.claim_for_prompt(new.id)
    assert m.mark_prompted(new.id)
    assert new.status == "prompted"


# ------------------------------------------------------------------ X1
def test_x1_confirm_requires_delivered_prompt():
    m = ConfirmationManager()
    a = m.create_pending("t", "X", {}, _ok)
    ok, _ = m.confirm(a.id, source="voice")     # pending — не озвучен
    assert ok is False
    assert m.claim_for_prompt(a.id)
    ok, _ = m.confirm(a.id, source="voice")     # prompting — ещё не озвучен
    assert ok is False
    assert m.get_active() is not None           # действие не потеряно


# ------------------------------------------------------- A1 (regression)
def test_a1_claim_vs_create_race():
    """Гонка: claim_for_prompt (доставка промпта) против create_pending
    (новая опасная просьба). Инвариант: либо claim победил и второй create
    блокирован, либо замена победила и claim отклонён. Никогда — claimed
    действие молча заменено."""
    for _ in range(100):
        m = ConfirmationManager()
        a = m.create_pending("t", "A", {}, _ok)
        barrier = threading.Barrier(2)
        results = {}

        def claimer():
            barrier.wait()
            results["claim"] = m.claim_for_prompt(a.id)

        def creator():
            barrier.wait()
            results["create"] = m.create_pending("t", "B", {}, _ok)

        t1 = threading.Thread(target=claimer)
        t2 = threading.Thread(target=creator)
        t1.start(); t2.start()
        t1.join(timeout=5); t2.join(timeout=5)
        assert not t1.is_alive() and not t2.is_alive()

        if results["claim"]:
            # claim победил: create блокирован, оригинал жив
            assert results["create"] is None
            assert m.get_active().id == a.id
            assert a.status == "prompting"
        else:
            # замена победила: create вернул новый, claim отклонён
            assert results["create"] is not None
            assert a.status == "discarded"
            assert m.get_active().id != a.id


# ------------------------------------------------------- A2 (regression)
def test_a2_expiry_atomic_under_concurrency():
    """Гонка: 8 потоков одновременно ловят истёкший action (confirm /
    get_active / reject). Инварианты: expired ровно один раз, executor не
    запускается, слот пуст, все наблюдатели согласны."""
    m = ConfirmationManager()
    runs = []
    a = m.create_pending("t", "X", {},
                         lambda p: runs.append(1) or "ok", ttl_seconds=0.05)
    _prompt(m, a)
    time.sleep(0.1)                     # action истёк

    barrier = threading.Barrier(8)
    outcomes = []
    lock = threading.Lock()

    def confirmer():
        barrier.wait()
        ok, _ = m.confirm(a.id, source="voice")
        with lock:
            outcomes.append(("confirm", ok))

    def querier():
        barrier.wait()
        with lock:
            outcomes.append(("active", m.get_active()))

    def rejector():
        barrier.wait()
        with lock:
            outcomes.append(("reject", m.reject(a.id)))

    threads = [threading.Thread(target=f) for f in
               (confirmer, confirmer, confirmer, confirmer,
                querier, querier, querier, rejector)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)
    assert all(not t.is_alive() for t in threads)

    assert not runs                     # executor не тронут
    assert a.status == "expired"        # единственный атомарный переход
    assert m.get_active() is None       # слот пуст
    for kind, val in outcomes:
        if kind == "confirm":
            assert val is False
        elif kind == "active":
            assert val is None
        else:
            assert val == "Нечего отменять."


if __name__ == "__main__":
    tests = [fn for name, fn in sorted(globals().items())
             if name.startswith("test_")]
    for fn in tests:
        fn()
        print(f"✅ {fn.__name__}")
    print(f"\nВсе {len(tests)} тестов пройдены.")
