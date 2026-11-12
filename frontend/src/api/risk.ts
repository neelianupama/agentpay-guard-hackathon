/**
 * Risk assessment API: POST /api/v1/risk/assess.
 *
 * Types mirror the backend Pydantic contracts (backend/app/schemas):
 * request = TransactionDetails, response = RiskAssessment.
 * The backend is the only authority for risk scores and decisions; this
 * module only transports them and checks the response matches the contract.
 */

import { ApiError, postJson, type RequestOptions } from './client'

export const RISK_ASSESS_PATH = '/api/v1/risk/assess'

/** Currencies offered in the UI. The backend still validates and scores every currency. */
export const CURRENCY_OPTIONS = ['USD', 'EUR', 'GBP', 'CAD', 'AUD', 'INR'] as const

export type RiskLevel = 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL'
export type PolicyDecision = 'ALLOW' | 'REVIEW' | 'BLOCK'

/** Backend `TransactionDetails`. `amount` is a decimal string to avoid float precision loss. */
export interface TransactionDetails {
  merchant_name: string
  amount: string
  currency: string
  description: string
  purpose: string
}

/** Backend `RiskAssessment`. */
export interface RiskAssessment {
  transaction: TransactionDetails
  risk_score: number
  risk_level: RiskLevel
  decision: PolicyDecision
  reasons: string[]
  assessed_at: string
}

const RISK_LEVELS: readonly RiskLevel[] = ['LOW', 'MEDIUM', 'HIGH', 'CRITICAL']
const DECISIONS: readonly PolicyDecision[] = ['ALLOW', 'REVIEW', 'BLOCK']
const TRANSACTION_FIELDS = ['merchant_name', 'amount', 'currency', 'description', 'purpose'] as const

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

/** Validate that an API response matches the RiskAssessment contract. */
export function parseRiskAssessment(data: unknown): RiskAssessment {
  const fail = (what: string): never => {
    throw new ApiError('invalid_response', `The risk service returned an unexpected response (${what}).`)
  }
  if (!isRecord(data)) return fail('not an object')

  const { transaction, risk_score, risk_level, decision, reasons, assessed_at } = data
  if (!isRecord(transaction) || !TRANSACTION_FIELDS.every((f) => typeof transaction[f] === 'string')) {
    return fail('transaction')
  }
  if (typeof risk_score !== 'number' || !Number.isInteger(risk_score) || risk_score < 0 || risk_score > 100) {
    return fail('risk_score')
  }
  if (!RISK_LEVELS.includes(risk_level as RiskLevel)) return fail('risk_level')
  if (!DECISIONS.includes(decision as PolicyDecision)) return fail('decision')
  if (!Array.isArray(reasons) || reasons.length === 0 || !reasons.every((r) => typeof r === 'string')) {
    return fail('reasons')
  }
  if (typeof assessed_at !== 'string' || Number.isNaN(Date.parse(assessed_at))) return fail('assessed_at')

  return {
    transaction: {
      merchant_name: transaction.merchant_name as string,
      amount: transaction.amount as string,
      currency: transaction.currency as string,
      description: transaction.description as string,
      purpose: transaction.purpose as string,
    },
    risk_score,
    risk_level: risk_level as RiskLevel,
    decision: decision as PolicyDecision,
    reasons: reasons as string[],
    assessed_at,
  }
}

/**
 * Ask the backend to assess a proposed payment.
 *
 * Only the five TransactionDetails fields are sent; the payload is built
 * explicitly so no other field (e.g. a score or decision) can be included.
 */
export async function assessRisk(
  details: TransactionDetails,
  options?: RequestOptions,
): Promise<RiskAssessment> {
  const payload: TransactionDetails = {
    merchant_name: details.merchant_name,
    amount: details.amount,
    currency: details.currency,
    description: details.description,
    purpose: details.purpose,
  }
  const data = await postJson(RISK_ASSESS_PATH, payload, options)
  return parseRiskAssessment(data)
}
