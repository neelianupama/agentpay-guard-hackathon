"""Payment records, state machine, repository and workflow concurrency (Step 4).

PayPal is the in-memory FakeSandbox (httpx.MockTransport); no network.
"""

import asyncio
from decimal import Decimal

import pytest

from app.schemas import PaymentStatus, TransactionDetails
from app.services.payments import (
    ALLOWED_TRANSITIONS,
    InMemoryPaymentRepository,
    InvalidPaymentTransitionError,
    PaymentBusyError,
    PaymentNotFoundError,
    PaymentProviderError,
    PaymentWorkflow,
    transition,
)
from app.services.payments.records import TERMINAL_STATUSES
from app.services.risk import RiskEngine
from tests.paypal_sandbox_fake import FakeSandbox

S = PaymentStatus

ALLOW = {"merchant_name": "Acme Cloud", "amount": Decimal("49.99"), "currency": "USD",
         "description": "Monthly hosting invoice", "purpose": "Infrastructure hosting"}
REVIEW = {**ALLOW, "amount": Decimal("2500.00")}


def run(coro):
    return asyncio.run(coro)


def make(sandbox=None, repo=None):
    sandbox = sandbox or FakeSandbox()
    repo = repo or InMemoryPaymentRepository()
    service = sandbox.service()
    return PaymentWorkflow(repo, RiskEngine(), lambda: service), sandbox, repo


# ================================================================ state machine


EXPECTED_EDGES = {
    (S.PENDING_REVIEW, S.APPROVED_FOR_PAYMENT), (S.PENDING_REVIEW, S.REJECTED),
    (S.APPROVED_FOR_PAYMENT, S.AWAITING_BUYER_APPROVAL), (S.APPROVED_FOR_PAYMENT, S.FAILED),
    (S.AWAITING_BUYER_APPROVAL, S.BUYER_APPROVED), (S.AWAITING_BUYER_APPROVAL, S.FAILED),
    (S.BUYER_APPROVED, S.AUTHORIZED), (S.BUYER_APPROVED, S.AUTHORIZATION_PENDING),
    (S.BUYER_APPROVED, S.FAILED),
    (S.AUTHORIZATION_PENDING, S.AUTHORIZED), (S.AUTHORIZATION_PENDING, S.FAILED),
    (S.AUTHORIZED, S.PENDING_CAPTURE), (S.AUTHORIZED, S.PENDING_VOID),
    (S.PENDING_CAPTURE, S.CAPTURED), (S.PENDING_CAPTURE, S.CAPTURE_PENDING),
    (S.PENDING_CAPTURE, S.FAILED),
    (S.PENDING_VOID, S.VOIDED), (S.PENDING_VOID, S.FAILED),
}


def test_transition_table_is_exactly_the_documented_one():
    actual = {(a, b) for a, nxt in ALLOWED_TRANSITIONS.items() for b in nxt}
    assert actual == EXPECTED_EDGES
    assert set(ALLOWED_TRANSITIONS) == set(PaymentStatus)


def test_terminal_statuses():
    assert TERMINAL_STATUSES == {S.BLOCKED, S.REJECTED, S.CAPTURE_PENDING, S.CAPTURED,
                                 S.VOIDED, S.FAILED}


def _record(status):
    workflow, _, repo = make()
    record = run(workflow.create_payment(TransactionDetails(**REVIEW)))
    record.status = status
    return record


@pytest.mark.parametrize("source", list(PaymentStatus))
def test_every_transition_is_validated(source):
    for target in PaymentStatus:
        record = _record(source)
        if (source, target) in EXPECTED_EDGES:
            transition(record, target, at=record.updated_at)
            assert record.status is target
        else:
            with pytest.raises(InvalidPaymentTransitionError):
                transition(record, target, at=record.updated_at)
            assert record.status is source


@pytest.mark.parametrize("target", [S.CAPTURED, S.AUTHORIZED, S.AWAITING_BUYER_APPROVAL])
def test_blocked_can_never_move(target):
    record = _record(S.BLOCKED)
    with pytest.raises(InvalidPaymentTransitionError):
        transition(record, target, at=record.updated_at)


# ==================================================================== repository


def test_repository_returns_copies():
    workflow, _, repo = make()
    record = run(workflow.create_payment(TransactionDetails(**REVIEW)))
    copy = repo.get(record.transaction_id)
    copy.status = S.CAPTURED
    copy.reasons = ("tampered",)
    assert repo.get(record.transaction_id).status is S.PENDING_REVIEW


def test_repository_unknown_id():
    repo = InMemoryPaymentRepository()
    with pytest.raises(PaymentNotFoundError):
        repo.get("txn_missing")


def test_records_use_decimal_money():
    workflow, _, _ = make()
    record = run(workflow.create_payment(TransactionDetails(**REVIEW)))
    assert isinstance(record.amount, Decimal) and record.amount == Decimal("2500.00")


def test_transaction_ids_are_unique_and_valid():
    workflow, _, _ = make()
    ids = {run(workflow.create_payment(TransactionDetails(**REVIEW))).transaction_id
           for _ in range(50)}
    assert len(ids) == 50
    assert all(i.startswith("txn_") and len(i) == 28 for i in ids)


# =================================================================== concurrency


async def _authorized(workflow, sandbox):
    record = await workflow.create_payment(TransactionDetails(**ALLOW))
    sandbox.buyer_approves(record.paypal_order_id)
    record = await workflow.authorize(record.transaction_id)
    assert record.status is S.AUTHORIZED
    return record.transaction_id


def test_concurrent_captures_capture_once():
    workflow, sandbox, repo = make()

    async def scenario():
        txn = await _authorized(workflow, sandbox)
        sandbox.delay_seconds = 0.05
        results = await asyncio.gather(
            *(workflow.capture(txn, reviewer_name=None, comment=None) for _ in range(10)),
            return_exceptions=True,
        )
        return txn, results

    txn, results = run(scenario())
    successes = [r for r in results if not isinstance(r, Exception)]
    busy = [r for r in results if isinstance(r, PaymentBusyError)]
    assert len(successes) == 1 and len(busy) == 9
    assert sandbox.counts["capture"] == 1
    assert repo.get(txn).status is S.CAPTURED


def test_concurrent_capture_and_void_only_one_wins():
    workflow, sandbox, repo = make()

    async def scenario():
        txn = await _authorized(workflow, sandbox)
        sandbox.delay_seconds = 0.05
        return txn, await asyncio.gather(
            workflow.capture(txn, reviewer_name=None, comment=None),
            workflow.void(txn, reviewer_name=None, comment=None),
            return_exceptions=True,
        )

    txn, (capture, void) = run(scenario())
    assert not isinstance(capture, Exception)
    assert isinstance(void, PaymentBusyError)
    assert sandbox.counts["capture"] == 1 and sandbox.counts["void"] == 0
    with pytest.raises(InvalidPaymentTransitionError):
        run(workflow.void(txn, reviewer_name=None, comment=None))


def test_concurrent_approvals_create_one_order():
    workflow, sandbox, _ = make()

    async def scenario():
        record = await workflow.create_payment(TransactionDetails(**REVIEW))
        sandbox.delay_seconds = 0.05
        return await asyncio.gather(
            *(workflow.approve(record.transaction_id, reviewer_name=None, comment=None)
              for _ in range(5)),
            return_exceptions=True,
        )

    results = run(scenario())
    assert sum(not isinstance(r, Exception) for r in results) == 1
    assert all(isinstance(r, (PaymentBusyError, InvalidPaymentTransitionError))
               for r in results if isinstance(r, Exception))
    assert sandbox.counts["create"] == 1


def test_concurrent_authorizations_authorize_once():
    workflow, sandbox, _ = make()

    async def scenario():
        record = await workflow.create_payment(TransactionDetails(**ALLOW))
        sandbox.buyer_approves(record.paypal_order_id)
        sandbox.delay_seconds = 0.05
        return await asyncio.gather(
            *(workflow.authorize(record.transaction_id) for _ in range(5)),
            return_exceptions=True,
        )

    results = run(scenario())
    assert sum(not isinstance(r, Exception) for r in results) == 1
    assert sandbox.counts["authorize"] == 1


# ============================================================ stable request IDs


def test_request_ids_are_generated_once_and_reused_across_ambiguous_failures():
    workflow, sandbox, repo = make()

    async def scenario():
        sandbox.inject("create", "lost", "timeout")
        with pytest.raises(PaymentProviderError):
            await workflow.create_payment(TransactionDetails(**ALLOW))
        (txn,) = [r.transaction_id for r in repo.list(limit=10, offset=0)[0]]
        first_id = repo.get(txn).create_request_id
        with pytest.raises(PaymentProviderError):
            await workflow.create_order(txn)
        assert repo.get(txn).create_request_id == first_id
        record = await workflow.create_order(txn)
        return record, first_id

    record, first_id = run(scenario())
    headers = [r.headers["paypal-request-id"] for r in sandbox.api_requests("/v2/checkout/orders")]
    assert headers == [first_id] * 3
    assert record.create_request_id == first_id
    assert sandbox.counts["create"] == 1


def test_capture_request_id_survives_failure_and_is_not_regenerated():
    workflow, sandbox, repo = make()

    async def scenario():
        txn = await _authorized(workflow, sandbox)
        sandbox.inject("capture", "lost", "unavailable", "timeout")
        ids = []
        for _ in range(3):
            with pytest.raises(PaymentProviderError) as exc_info:
                await workflow.capture(txn, reviewer_name=None, comment=None)
            assert exc_info.value.details["retry_safe"] is True
            ids.append(repo.get(txn).capture_request_id)
        record = await workflow.capture(txn, reviewer_name=None, comment=None)
        return ids, record

    ids, record = run(scenario())
    assert len(set(ids)) == 1 and record.capture_request_id == ids[0]
    assert {r.headers["paypal-request-id"] for r in sandbox.api_requests("/capture")} == {ids[0]}
    assert record.status is S.CAPTURED
    assert sandbox.counts["capture"] == 1
    assert [a.action for a in record.reviewer_actions] == ["capture"] * 4


def test_no_paypal_calls_for_block_or_review_at_workflow_level():
    workflow, sandbox, _ = make()
    block = {**ALLOW, "amount": Decimal("3000.00"), "description": "Urgent bitcoin purchase"}
    assert run(workflow.create_payment(TransactionDetails(**block))).status is S.BLOCKED
    assert run(workflow.create_payment(TransactionDetails(**REVIEW))).status is S.PENDING_REVIEW
    assert sandbox.requests == []
