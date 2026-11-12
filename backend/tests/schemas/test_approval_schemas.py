"""ApprovalRequest validation."""

from app.schemas import ApprovalDecision, ApprovalRequest
from tests.helpers import assert_invalid


def test_approval_without_comment():
    approval = ApprovalRequest(transaction_id="txn_1", decision="APPROVE")
    assert approval.decision is ApprovalDecision.APPROVE
    assert approval.comment is None


def test_approval_with_comment():
    approval = ApprovalRequest(transaction_id="txn_1", decision=ApprovalDecision.REJECT, comment="dup")
    assert approval.decision is ApprovalDecision.REJECT
    assert approval.comment == "dup"


def test_unknown_approval_decision_rejected():
    assert_invalid(
        lambda: ApprovalRequest(transaction_id="txn_1", decision="MAYBE"), "decision", "enum"
    )


def test_empty_transaction_id_rejected():
    assert_invalid(
        lambda: ApprovalRequest(transaction_id="", decision="APPROVE"),
        "transaction_id",
        "string_too_short",
    )
