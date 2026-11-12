"""In-memory payment repository.

LIMITATION: records live only in this process's memory. They are lost when
the backend restarts and are not shared between multiple worker processes.
Run a single worker until database persistence is implemented. (A restart can
therefore lose the stored PayPal IDs and idempotency request IDs of payments
in flight; reconcile such payments in the PayPal Sandbox dashboard.)

Concurrency: each record has its own ``asyncio.Lock``. Workflow actions take
the lock *without waiting*; a second concurrent action on the same record is
rejected immediately (``PaymentBusyError``) instead of queueing a duplicate.
"""

from __future__ import annotations

import asyncio
import copy
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from app.services.payments.errors import PaymentBusyError, PaymentNotFoundError
from app.services.payments.records import PaymentRecord


def new_transaction_id() -> str:
    return f"txn_{secrets.token_hex(12)}"


class InMemoryPaymentRepository:
    def __init__(self) -> None:
        self._records: dict[str, PaymentRecord] = {}
        self._order: list[str] = []  # insertion order (oldest first)
        self._locks: dict[str, asyncio.Lock] = {}

    def add(self, record: PaymentRecord) -> None:
        if record.transaction_id in self._records:
            raise ValueError("duplicate transaction_id")
        self._records[record.transaction_id] = copy.deepcopy(record)
        self._order.append(record.transaction_id)
        self._locks[record.transaction_id] = asyncio.Lock()

    def get(self, transaction_id: str) -> PaymentRecord:
        """Return a copy; changes are only persisted through :meth:`save`."""
        record = self._records.get(transaction_id)
        if record is None:
            raise PaymentNotFoundError()
        return copy.deepcopy(record)

    def save(self, record: PaymentRecord) -> None:
        if record.transaction_id not in self._records:
            raise PaymentNotFoundError()
        self._records[record.transaction_id] = copy.deepcopy(record)

    def list(self, *, limit: int, offset: int) -> tuple[list[PaymentRecord], int]:
        """Newest first."""
        ids = list(reversed(self._order))
        page = ids[offset : offset + limit]
        return [copy.deepcopy(self._records[i]) for i in page], len(ids)

    @asynccontextmanager
    async def locked(self, transaction_id: str) -> AsyncIterator[PaymentRecord]:
        """Exclusive access to one record, failing fast if it is already in use."""
        lock = self._locks.get(transaction_id)
        if lock is None:
            raise PaymentNotFoundError()
        if lock.locked():
            raise PaymentBusyError(transaction_id=transaction_id)
        async with lock:
            yield self.get(transaction_id)

    def clear(self) -> None:
        self._records.clear()
        self._order.clear()
        self._locks.clear()
