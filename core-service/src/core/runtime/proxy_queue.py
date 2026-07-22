"""
core/runtime/proxy_queue.py — черга HTTP-запитів per-proxy з пріоритетами
та circuit breaker.

Замінює proxy_rate_limiter + RateLimiter повністю.
───────────────────────────────────────────────────
Один asyncio-воркер на кожен унікальний проксі. Запити виконуються
послідовно, впорядковані за Priority (нижче число = вищий пріоритет,
виконується першим). Між завершенням одного запиту і початком наступного
воркер витримує REQUEST_DELAY.

Нове порівняно з попередньою версією:
  1. Priority-черга: AUTH-запити (логін, реєстрація/підключення акаунта,
     re-login після 419/401) завжди обробляються попереду NORMAL/BACKGROUND
     запитів, навіть якщо остання прийшли раніше і чекають у черзі.
  2. Реальний retry: caller передає coroutine factory (Callable, що
     повертає нову корутину при кожному виклику), а не готову корутину.
     Раніше retry на RateLimitedError/мережевій помилці був неможливий,
     бо корутина вичерпувалась після першого await — фактично retry-цикл
     існував лише "на папері" (див. старий FIX Bug 1 коментар).
  3. Circuit breaker per-воркер: після N поспіль мережевих помилок
     (timeout / connection error) воркер переходить у стан OPEN і одразу
     (без реального HTTP-виклику) відхиляє нові завдання протягом
     cooldown-періоду, що зростає експоненційно. Це прибирає "непередбачувану"
     поведінку під час зовнішнього збою мережі: замість того, щоб кожен
     акаунт окремо тайм-аутився по 15-60с і засмічував чергу, воркер
     миттєво і прогнозовано відмовляє новим запитам, поки не пройде
     cooldown. Коли cooldown минає — воркер переходить у HALF_OPEN і
     пропускає ОДНЕ завдання-пробу (природно це буде найвищий пріоритет
     у черзі, тобто AUTH, якщо він там є) щоб перевірити відновлення.
  4. Дренаж "мертвих" завдань: якщо caller вже відмовився чекати
     (Future/задача була скасована через RequestContext.timeout ще до
     того, як воркер дістав її з черги), воркер пропускає виконання
     замість того, щоб витрачати на нього REQUEST_DELAY і мережевий виклик.

Структура:
    ProxyQueueManager (singleton)
    ├── "http://1.2.3.4:8080" → _ProxyWorker → PriorityQueue → sequential
    ├── "http://5.6.7.8:3128" → _ProxyWorker → PriorityQueue → sequential
    └── "__no_proxy__"        → _ProxyWorker → PriorityQueue → sequential

Використання в BotHttpClient:
    return await proxy_queue_manager.enqueue_coro(
        proxy        = self.bot_config.network.proxy,
        coro_factory = lambda: self._client.get(url, **kwargs),
        label        = f"GET {url}",
        priority     = Priority.AUTH,   # або NORMAL / BACKGROUND
    )
"""
from __future__ import annotations

import asyncio
import hashlib
import itertools
import logging
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Callable, Coroutine, Dict, Optional, TypeVar
from urllib.parse import urlparse

T = TypeVar("T")

log = logging.getLogger("core.proxy_queue")

REQUEST_DELAY: float = 3.0
RETRY_AFTER_DEFAULT: float = 15.0
MAX_RETRIES: int = 5

# Circuit breaker налаштування.
CB_FAILURE_THRESHOLD: int = 3        # поспіль мережевих помилок до OPEN
CB_BASE_COOLDOWN: float = 10.0       # перший cooldown, сек
CB_MAX_COOLDOWN: float = 180.0       # стеля cooldown, сек
CB_BACKOFF_MULTIPLIER: float = 2.0

_NO_PROXY_KEY = "__no_proxy__"


class Priority(IntEnum):
    """Менше значення = вищий пріоритет = виконується першим."""
    AUTH          = 0   # логін, реєстрація/підключення акаунта, re-login на 419/401
    TIME_CRITICAL = 5   # дії із жорстким зовнішнім дедлайном, що спливає за
                         # секунди (напр. вікно відповіді у квізі) — мають
                         # обганяти навіть CRITICAL, бо запізнення = провал
                         # дії як такої, а не просто затримка. Не обганяють
                         # AUTH: без валідної сесії сам запит все одно 401/419.
    CRITICAL      = 10  # явні дії користувача/адміна, що не можна відкладати
    NORMAL        = 20  # звичайні бізнес-дії (mining_hit, ...)
    BACKGROUND    = 30  # фонові/масові опитування, не критичні до затримки


class RateLimitedError(Exception):
    def __init__(self, retry_after: float = RETRY_AFTER_DEFAULT) -> None:
        self.retry_after = retry_after
        super().__init__(f"429 Too Many Requests, retry after {retry_after:.1f}s")


class CircuitOpenError(Exception):
    """Воркер швидко відмовляє запиту, бо для цього проксі відкрито circuit breaker."""
    def __init__(self, key: str, retry_after: float) -> None:
        self.key = key
        self.retry_after = retry_after
        super().__init__(
            f"[ProxyWorker:{key}] circuit open — fast-fail, retry after {retry_after:.1f}s"
        )


class ProxyFatalError(Exception):
    """
    Проксі визнано фатально непрацездатним (а не тимчасово недоступним).

    На відміну від звичайних мережевих помилок (таймаут, розрив з'єднання —
    ретраються з backoff'ом і circuit breaker'ом), ProxyFatalError означає,
    що подальші спроби через це проксі безглузді: сам проксі-сервер не
    приймає з'єднання, не резолвиться, вимагає авторизацію тощо. Піднімається
    одразу, без retry, і worker переводить circuit breaker у OPEN.

    Caller (BotHttpClient) на цей виняток одразу сигналізує
    ProxyFatalCallback — типово Account.mark_dead() — щоб акаунт миттєво
    перейшов у DEAD, ще до того, як звичайна обробка помилок (re-login,
    FailReason.NETWORK тощо) встигне замаскувати справжню причину.
    """
    def __init__(self, key: str, detail: str) -> None:
        self.key = key
        self.detail = detail
        super().__init__(f"[ProxyWorker:{key}] fatal proxy error: {detail}")


# Мережеві помилки (curl_cffi/aiohttp тощо) — ретраюємо і рахуємо в circuit breaker.
# Використовуємо duck-typing замість жорсткої залежності від curl_cffi,
# щоб цей модуль лишався транспортно-агностичним.
_NETWORK_EXC_BASES = (asyncio.TimeoutError, ConnectionError, OSError)

# Ознаки того, що зламалось саме ПРОКСІ (а не цільовий сайт) і відновлення
# неможливе без заміни проксі: невдале резолвлення/з'єднання з самим
# проксі-сервером, відмова в авторизації на ньому, зірваний CONNECT-тунель.
# Розпізнаємо за текстом помилки — curl_cffi не завжди дає окремий клас
# на кожен випадок, а формулювання достатньо стабільні для CURL-помилок.
_PROXY_FATAL_PATTERNS = (
    "couldn't resolve proxy",
    "could not resolve proxy",
    "couldn't connect to proxy",
    "could not connect to proxy",
    "failed to connect to proxy",
    "proxy connect aborted",
    "proxy connection failed",
    "proxy connection refused",
    "unsupported proxy",
    "proxy authentication",
    "407 proxy authentication required",
    "tunnel connection failed",
    "socks connect",
    "socks5",
    "proxy_ssl",
)


def _is_proxy_fatal_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    if any(pattern in msg for pattern in _PROXY_FATAL_PATTERNS):
        return True
    mod = type(exc).__module__
    name = type(exc).__name__
    if "curl_cffi" in mod and "Proxy" in name:
        return True
    return False


def _is_network_error(exc: BaseException) -> bool:
    if isinstance(exc, _NETWORK_EXC_BASES):
        return True
    # curl_cffi піднімає власні класи (CurlError, Timeout, ConnectionError),
    # які не завжди успадковують стандартні — розпізнаємо за модулем/назвою.
    mod = type(exc).__module__
    name = type(exc).__name__
    if "curl_cffi" in mod and ("Timeout" in name or "Connection" in name or "Curl" in name):
        return True
    return False


class _CircuitState(IntEnum):
    CLOSED = 0
    OPEN = 1
    HALF_OPEN = 2


class _CircuitBreaker:
    """
    Один circuit breaker на _ProxyWorker. Не потребує локів — воркер
    обробляє завдання строго послідовно (один _execute() в моменті),
    тому стан змінюється лише з одного "потоку" виконання.
    """

    def __init__(self, key: str) -> None:
        self._key = key
        self._state = _CircuitState.CLOSED
        self._consecutive_failures = 0
        self._cooldown = CB_BASE_COOLDOWN
        self._opened_at = 0.0

    def _maybe_transition_to_half_open(self) -> None:
        if self._state == _CircuitState.OPEN:
            if time.monotonic() - self._opened_at >= self._cooldown:
                self._state = _CircuitState.HALF_OPEN
                log.info(f"[ProxyWorker:{self._key}] circuit HALF_OPEN — probing")

    def should_allow(self) -> bool:
        self._maybe_transition_to_half_open()
        return self._state != _CircuitState.OPEN

    def remaining_cooldown(self) -> float:
        if self._state != _CircuitState.OPEN:
            return 0.0
        return max(0.0, self._cooldown - (time.monotonic() - self._opened_at))

    def record_success(self) -> None:
        if self._state != _CircuitState.CLOSED:
            log.info(f"[ProxyWorker:{self._key}] circuit CLOSED — recovered")
        self._state = _CircuitState.CLOSED
        self._consecutive_failures = 0
        self._cooldown = CB_BASE_COOLDOWN

    def record_failure(self) -> bool:
        """Повертає True, якщо ця помилка щойно відкрила (або знову відкрила) circuit."""
        if self._state == _CircuitState.HALF_OPEN:
            # Проба провалилась — знову відкриваємо, з більшим cooldown.
            self._open(escalate=True)
            return True

        self._consecutive_failures += 1
        if self._consecutive_failures >= CB_FAILURE_THRESHOLD:
            self._open(escalate=False)
            return True
        return False

    def _open(self, escalate: bool) -> None:
        if escalate and self._state == _CircuitState.HALF_OPEN:
            self._cooldown = min(self._cooldown * CB_BACKOFF_MULTIPLIER, CB_MAX_COOLDOWN)
        self._state = _CircuitState.OPEN
        self._opened_at = time.monotonic()
        self._consecutive_failures = 0
        log.warning(
            f"[ProxyWorker:{self._key}] circuit OPEN — fast-failing new requests "
            f"for {self._cooldown:.0f}s"
        )

    @property
    def is_probe_slot(self) -> bool:
        return self._state == _CircuitState.HALF_OPEN

    def force_open(self) -> None:
        """
        Негайно відкриває circuit — проксі визнано фатально непрацездатним
        (ProxyFatalError), а не просто тимчасово недоступним. Наступні
        завдання цього воркера швидко відмовляються, поки хтось (оператор)
        не замінить проксі і не перепідключить акаунт.
        """
        self._open(escalate=False)


_SENTINEL_PRIORITY = -1000  # завжди попереду всього — для швидкого shutdown


@dataclass(order=True)
class _Task:
    priority: int
    seq:      int
    coro_factory: Optional[Callable[[], Coroutine[Any, Any, Any]]] = field(compare=False, default=None)
    future:       Optional["asyncio.Future[Any]"]                  = field(compare=False, default=None)
    label:        str                                              = field(compare=False, default="")
    enqueued_at:  float = field(compare=False, default_factory=time.monotonic)

    @property
    def is_sentinel(self) -> bool:
        return self.coro_factory is None


class _ProxyWorker:
    def __init__(self, key: str) -> None:
        self._key = key
        self._queue: Optional["asyncio.PriorityQueue[_Task]"] = None
        self._task: Optional[asyncio.Task[None]] = None
        self._shutting_down: bool = False
        self._seq = itertools.count()
        self._circuit = _CircuitBreaker(key)

    def _get_queue(self) -> "asyncio.PriorityQueue[_Task]":
        if self._queue is None:
            self._queue = asyncio.PriorityQueue()
        return self._queue

    def _ensure_task_started(self) -> None:
        """
        Запускає _run()-task у поточному running loop.
        Викликається ліниво при першому enqueue() — гарантує що task
        прив'язаний до того самого loop, що й усі майбутні enqueue/shutdown.

        Якщо попередній task мертвий (loop перезапустився) — стартує новий.
        """
        loop = asyncio.get_running_loop()
        if (
            self._task is None
            or self._task.done()
            or self._task.get_loop() is not loop
        ):
            if self._queue is not None:
                while True:
                    try:
                        orphan = self._queue.get_nowait()
                        if not orphan.is_sentinel and orphan.future is not None and not orphan.future.done():
                            orphan.future.set_exception(
                                RuntimeError(
                                    f"[ProxyWorker:{self._key}] worker restarted — task cancelled"
                                )
                            )
                    except asyncio.QueueEmpty:
                        break
            self._queue = asyncio.PriorityQueue()
            self._shutting_down = False
            self._task = asyncio.create_task(self._run(), name=f"proxy-worker-{self._key}")
            log.debug(f"[ProxyWorker:{self._key}] task started in loop {id(loop)}")

    def start(self) -> None:
        """
        Залишено для зворотної сумісності (викликається з ensure()).
        Реальний старт task відкладено до першого enqueue() — щоб task
        гарантовано прив'язався до правильного loop (scheduler's, не main's).
        """
        log.debug(f"[ProxyWorker:{self._key}] registered (lazy start)")

    def enqueue(
        self,
        coro_factory: Callable[[], Coroutine[Any, Any, T]],
        label: str = "",
        priority: Priority = Priority.NORMAL,
    ) -> "asyncio.Future[T]":
        if self._shutting_down:
            loop = asyncio.get_running_loop()
            fut: "asyncio.Future[T]" = loop.create_future()
            fut.set_exception(RuntimeError(
                f"[ProxyWorker:{self._key}] worker is shutting down — enqueue rejected"
            ))
            return fut

        # Lazy start: task стартує тут — завжди в правильному running loop.
        self._ensure_task_started()
        loop = asyncio.get_running_loop()
        fut: "asyncio.Future[T]" = loop.create_future()
        task = _Task(
            priority=int(priority),
            seq=next(self._seq),
            coro_factory=coro_factory,
            future=fut,
            label=label,
        )
        self._get_queue().put_nowait(task)
        return fut

    async def shutdown(self) -> None:
        self._shutting_down = True

        if self._queue is not None:
            self._queue.put_nowait(_Task(priority=_SENTINEL_PRIORITY, seq=next(self._seq)))

        if self._task is None or self._task.done():
            return

        try:
            current_loop = asyncio.get_running_loop()
            if self._task.get_loop() is not current_loop:
                log.warning(
                    f"[ProxyWorker:{self._key}] task loop mismatch — force cancel"
                )
                self._task.cancel()
            else:
                await asyncio.wait_for(self._task, timeout=5.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            if self._task:
                self._task.cancel()
        except RuntimeError as e:
            log.warning(f"[ProxyWorker:{self._key}] shutdown runtime error: {e}")
            if self._task:
                self._task.cancel()

    @property
    def queue_size(self) -> int:
        return self._queue.qsize() if self._queue else 0

    async def _run(self) -> None:
        queue = self._get_queue()
        log.debug(f"[ProxyWorker:{self._key}] loop started")
        while True:
            task = await queue.get()
            if task.is_sentinel:
                queue.task_done()
                break

            # Абонент вже здався (RequestContext.timeout спрацював раніше,
            # ніж черга дійшла до цього завдання) — не витрачаємо на нього
            # мережевий виклик і REQUEST_DELAY.
            if task.future is not None and task.future.done():
                queue.task_done()
                log.debug(
                    f"[ProxyWorker:{self._key}] skip abandoned task {task.label!r} "
                    f"(waited {time.monotonic() - task.enqueued_at:.1f}s in queue)"
                )
                continue

            wait_time = time.monotonic() - task.enqueued_at
            if wait_time > 5.0:
                log.warning(
                    f"[ProxyWorker:{self._key}] {task.label!r} waited {wait_time:.1f}s "
                    f"in queue before execution (queue_size={queue.qsize()})"
                )

            await self._execute(task)
            queue.task_done()
            # Затримка завжди, незалежно від наявності наступного завдання
            # у черзі — інакше два запити, що надійшли підряд, виконуються
            # без паузи між ними.
            await asyncio.sleep(REQUEST_DELAY)
        log.debug(f"[ProxyWorker:{self._key}] loop finished")

    async def _execute(self, task: _Task) -> None:
        label = task.label or "<anonymous>"
        assert task.coro_factory is not None
        assert task.future is not None

        if not self._circuit.should_allow():
            exc = CircuitOpenError(self._key, self._circuit.remaining_cooldown())
            log.warning(f"[ProxyWorker:{self._key}] fast-fail {label!r}: {exc}")
            if not task.future.done():
                task.future.set_exception(exc)
            return

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                result = await task.coro_factory()
                self._circuit.record_success()
                if not task.future.done():
                    task.future.set_result(result)
                return

            except RateLimitedError as e:
                log.warning(
                    f"[ProxyWorker:{self._key}] 429 on {label!r} "
                    f"(attempt {attempt}/{MAX_RETRIES}), "
                    f"freezing for {e.retry_after:.1f}s"
                )
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(e.retry_after)
                    continue  # coro_factory() створить нову корутину — реальний retry
                if not task.future.done():
                    task.future.set_exception(e)
                return

            except Exception as exc:
                if _is_proxy_fatal_error(exc):
                    # Проксі фатально непрацездатне — ретраї безглузді.
                    # Одразу відкриваємо circuit і піднімаємо ProxyFatalError,
                    # щоб BotHttpClient міг миттєво позначити акаунт DEAD.
                    self._circuit.force_open()
                    log.error(
                        f"[ProxyWorker:{self._key}] FATAL proxy error on {label!r}: {exc}"
                    )
                    if not task.future.done():
                        task.future.set_exception(ProxyFatalError(self._key, str(exc)))
                    return

                if _is_network_error(exc):
                    just_opened = self._circuit.record_failure()
                    log.warning(
                        f"[ProxyWorker:{self._key}] network error on {label!r} "
                        f"(attempt {attempt}/{MAX_RETRIES}): {exc}"
                    )
                    if just_opened or attempt >= MAX_RETRIES:
                        # Circuit щойно відкрився АБО спроби вичерпано —
                        # не бомбардуємо мертву мережу далі.
                        if not task.future.done():
                            task.future.set_exception(exc)
                        return
                    backoff = min(2 ** (attempt - 1), 8.0)
                    await asyncio.sleep(backoff)
                    continue

                # Не мережева помилка (логічна/додаткова) — ретраїти сенсу немає.
                log.error(
                    f"[ProxyWorker:{self._key}] error on {label!r}: {exc}",
                    exc_info=True,
                )
                if not task.future.done():
                    task.future.set_exception(exc)
                return

        exc = RuntimeError(f"[ProxyWorker:{self._key}] {label!r} failed after {MAX_RETRIES} retries")
        log.error(str(exc))
        if not task.future.done():
            task.future.set_exception(exc)


class ProxyQueueManager:
    def __init__(self) -> None:
        self._workers: Dict[str, _ProxyWorker] = {}

    def _key(self, proxy: Optional[str]) -> str:
        """
        Обчислює ключ воркера для проксі.

        КРИТИЧНО: ключ повинен однозначно ідентифікувати САМЕ ПРОКСІ
        (реальний вихідний IP), а не просто мережеву адресу шлюзу.

        У ротаційних проксі-провайдерів (Bright Data, IPRoyal, SOAX,
        Oxylabs тощо) усі акаунти зазвичай ходять через ОДИН і той самий
        host:port (шлюз), а РЕАЛЬНИЙ вихідний IP/сесію визначають саме
        user:pass у самому proxy-рядку (напр. session-id закодований у
        username). Якщо будувати ключ лише з host:port — два різні акаунти
        з різними сесіями (різними реальними проксі) фізично зіллються в
        ОДИН воркер: серіалізуються один за одним, ділять один і той самий
        circuit breaker (поламана сесія одного акаунта відкриває circuit і
        миттєво фейлить запити зовсім іншого, здорового акаунта) — це і
        є причина «акаунти потрапляють не в ту чергу».

        Тому ключ = людинозрозумілий префікс (scheme://host:port, лише для
        зручності читання логів) + fingerprint (короткий sha1) від ПОВНОГО
        сирого рядка проксі (включно з credentials). Однакові рядки проксі
        завжди дають однаковий ключ (навмисне спільне проксі коректно
        серіалізується), а будь-яка відмінність у рядку — гарантовано інший
        ключ. Пароль у відкритому вигляді в лог/ключ не потрапляє.
        """
        if not proxy:
            return _NO_PROXY_KEY

        proxy_str = proxy.strip()
        if not proxy_str:
            return _NO_PROXY_KEY

        fingerprint = hashlib.sha1(proxy_str.encode("utf-8")).hexdigest()[:10]

        try:
            normalized = proxy_str if "://" in proxy_str else f"http://{proxy_str}"
            p = urlparse(normalized)
            host = p.hostname or ""
            if not host:
                # Не вдалось витягти host — не намагаємось нормалізувати
                # далі: fingerprint сирого рядка вже гарантує унікальність.
                return f"raw#{fingerprint}"
            port = f":{p.port}" if p.port else ""
            prefix = f"{p.scheme.lower()}://{host.lower()}{port}"
        except Exception:
            return f"raw#{fingerprint}"

        return f"{prefix}#{fingerprint}"

    def ensure(self, proxy: Optional[str]) -> _ProxyWorker:
        key = self._key(proxy)
        if key not in self._workers:
            worker = _ProxyWorker(key)
            worker.start()
            self._workers[key] = worker
            log.info(f"[ProxyQueueManager] new worker for proxy={key!r}")
        return self._workers[key]

    async def enqueue_coro(
        self,
        proxy: Optional[str],
        coro_factory: Callable[[], Coroutine[Any, Any, T]],
        label: str = "",
        priority: Priority = Priority.NORMAL,
    ) -> T:
        """
        Виконує корутину, породжену coro_factory(), та повертає результат T.
        coro_factory викликається заново на кожній спробі retry — це і є
        фікс головного бага попередньої версії (retry на вже вичерпаній
        корутині був неможливий).
        """
        worker = self.ensure(proxy)
        return await worker.enqueue(coro_factory, label=label, priority=priority)

    def queue_sizes(self) -> Dict[str, int]:
        return {k: w.queue_size for k, w in self._workers.items()}

    async def shutdown_all(self) -> None:
        keys = list(self._workers.keys())
        for key in keys:
            worker = self._workers[key]
            log.info(f"[ProxyQueueManager] shutting down {key!r}")
            try:
                await worker.shutdown()
            except Exception as e:
                log.error(f"Error shutting down worker {key}: {e}")
            finally:
                del self._workers[key]


proxy_queue_manager = ProxyQueueManager()