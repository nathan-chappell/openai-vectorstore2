import { useEffect, useMemo, useState } from "react";

import { AdminPortfolioPanel } from "../../../vendor/ai-portfolio-admin/frontend";
import type {
  AdminPortfolioPanelCallbacks,
  AdminUserSummary as SharedAdminUserSummary,
  CreditGrantRecord,
  FreeCreditRequestRecord as SharedFreeCreditRequestRecord,
  ManualCreditGrantRequest,
  PaymentAttemptRecord as SharedPaymentAttemptRecord,
} from "../../../vendor/ai-portfolio-admin/frontend";
import {
  createFreeCreditRequest,
  createPayPalPaymentAttempt,
  decideAdminFreeCreditRequest,
  decideAdminPaymentAttempt,
  getPaymentIntegrationStatus,
  grantAdminCredit,
  listAdminFreeCreditRequests,
  listAdminPaymentAttempts,
  listAdminUsers,
  listFreeCreditRequests,
  listPayPalPaymentAttempts,
  setAdminUserActive,
  uploadPayPalReceipt,
} from "../lib/api";
import type {
  AdminGrantCreditResponse,
  AdminSetUserActiveResponse,
  AdminUserSummary as LocalAdminUserSummary,
  AuthUser,
  FreeCreditRequestSummary,
  PaymentAttemptSummary,
  PaymentIntegrationResponse,
} from "../lib/types";

function toSharedRole(role: string | null): SharedAdminUserSummary["role"] {
  return role === "admin" ? "admin" : "user";
}

function toSharedUser(user: LocalAdminUserSummary): SharedAdminUserSummary {
  return {
    user_id: user.clerk_user_id,
    email: user.primary_email,
    display_name: user.display_name,
    image_url: user.image_url,
    role: toSharedRole(user.role),
    is_active: user.active,
    current_credit_usd: user.current_credit_usd,
    credit_floor_usd: user.credit_floor_usd,
    created_at_ms: user.created_at_ms,
    last_sign_in_at_ms: user.last_sign_in_at_ms,
  };
}

function updateSharedUser(
  userId: string,
  users: SharedAdminUserSummary[],
  response: AdminSetUserActiveResponse,
): SharedAdminUserSummary {
  const current = users.find((candidate) => candidate.user_id === userId);
  return {
    user_id: response.clerk_user_id,
    email: current?.email ?? null,
    display_name: current?.display_name ?? null,
    image_url: current?.image_url ?? null,
    role: current?.role ?? "user",
    is_active: response.active,
    current_credit_usd: response.current_credit_usd,
    credit_floor_usd: response.credit_floor_usd,
    created_at_ms: current?.created_at_ms ?? null,
    last_sign_in_at_ms: current?.last_sign_in_at_ms ?? null,
  };
}

function toSharedGrant(payload: ManualCreditGrantRequest, response: AdminGrantCreditResponse): CreditGrantRecord {
  return {
    id: response.grant.id,
    user_id: response.grant.clerk_user_id,
    admin_user_id: response.grant.admin_clerk_user_id,
    credit_amount_usd: response.grant.credit_amount_usd,
    source: payload.source ?? "admin_manual",
    note: response.grant.note,
    request_id: payload.request_id ?? null,
    payment_provider: response.grant.payment_provider,
    payment_reference: response.grant.payment_reference,
    resulting_balance_usd: response.balance.current_credit_usd,
    created_at: response.grant.created_at,
  };
}

function toSharedPaymentAttempt(attempt: PaymentAttemptSummary): SharedPaymentAttemptRecord {
  return {
    id: attempt.id,
    user_id: attempt.clerk_user_id,
    provider: "paypal",
    expected_amount_usd: attempt.expected_amount_usd,
    expected_currency: attempt.expected_currency,
    reference_code: attempt.reference_code,
    status: attempt.status,
    temporary_access_expires_at: attempt.temporary_access_expires_at,
    provider_reference: attempt.provider_reference,
    created_at: attempt.created_at,
  };
}

function toSharedFreeCreditRequest(request: FreeCreditRequestSummary): SharedFreeCreditRequestRecord {
  return {
    id: request.id,
    user_id: request.clerk_user_id,
    requested_amount_usd: request.requested_amount_usd,
    source: request.source,
    reason: request.reason,
    linkedin_profile_url: request.linkedin_profile_url,
    relationship_note: request.relationship_note,
    intended_use: request.intended_use,
    evidence_verified: request.evidence_verified,
    idempotency_key: request.idempotency_key,
    status: request.status,
    decided_amount_usd: request.decided_amount_usd,
    decision_note: request.decision_note,
    reviewer_user_id: request.reviewer_clerk_user_id,
    credit_grant_id: request.credit_grant_id,
    created_at: request.created_at,
    decided_at: request.decided_at,
  };
}

export function AdminWorkspacePanel({ user }: { user: AuthUser | null }) {
  const callbacks = useMemo<AdminPortfolioPanelCallbacks>(() => {
    let latestUsers: SharedAdminUserSummary[] = [];
    return {
      async searchUsers(query, offset, limit) {
        const response = await listAdminUsers({ query, offset, limit });
        latestUsers = response.items.map(toSharedUser);
        return {
          items: latestUsers,
          has_more: response.has_more,
        };
      },
      async setUserActive(userId, active) {
        const response = await setAdminUserActive({
          clerk_user_id: userId,
          active,
        });
        const updated = updateSharedUser(userId, latestUsers, response);
        latestUsers = latestUsers.map((candidate) => (candidate.user_id === userId ? updated : candidate));
        return updated;
      },
      async grantCredit(payload) {
        const response = await grantAdminCredit({
          clerk_user_id: payload.user_id,
          credit_amount_usd: payload.credit_amount_usd,
          note: payload.note,
        });
        return toSharedGrant(payload, response);
      },
      async listFreeCreditRequests(status) {
        const response = await listAdminFreeCreditRequests(status);
        return response.requests.map(toSharedFreeCreditRequest);
      },
      async decideFreeCreditRequest(payload) {
        const response = await decideAdminFreeCreditRequest({
          request_id: payload.request_id,
          status: payload.status,
          credit_amount_usd: payload.credit_amount_usd,
          decision_note: payload.decision_note,
        });
        return toSharedFreeCreditRequest(response);
      },
      async listPaymentAttempts(status) {
        const response = await listAdminPaymentAttempts(status);
        return response.attempts.map(toSharedPaymentAttempt);
      },
      async decidePaymentAttempt(payload) {
        const response = await decideAdminPaymentAttempt({
          attempt_id: payload.attempt_id,
          status: payload.status,
          decision_note: payload.decision_note,
          credit_amount_usd: payload.credit_amount_usd,
          provider_reference: payload.provider_reference,
        });
        return toSharedPaymentAttempt(response);
      },
    };
  }, []);

  if (user?.role !== "admin") {
    return <AccountWorkspacePanel user={user} />;
  }

  return (
    <section className="admin-workspace-panel" aria-label="Admin users and credits">
      <AdminPortfolioPanel callbacks={callbacks} />
    </section>
  );
}

function formatUsd(value: number): string {
  return `$${value.toFixed(2)}`;
}

function AccountWorkspacePanel({ user }: { user: AuthUser | null }) {
  const [paymentStatus, setPaymentStatus] = useState<PaymentIntegrationResponse | null>(null);
  const [paymentAttempts, setPaymentAttempts] = useState<PaymentAttemptSummary[]>([]);
  const [freeCreditRequests, setFreeCreditRequests] = useState<FreeCreditRequestSummary[]>([]);
  const [paymentAmount, setPaymentAmount] = useState("10.00");
  const [freeCreditAmount, setFreeCreditAmount] = useState("5.00");
  const [freeCreditReason, setFreeCreditReason] = useState("");
  const [selectedReceipt, setSelectedReceipt] = useState<File | null>(null);
  const [activeAttemptId, setActiveAttemptId] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setStatus(null);
    void Promise.all([getPaymentIntegrationStatus(), listPayPalPaymentAttempts(), listFreeCreditRequests()])
      .then(([response, attemptResponse, freeCreditResponse]) => {
        if (!cancelled) {
          setPaymentStatus(response);
          setPaymentAttempts(attemptResponse.attempts);
          setFreeCreditRequests(freeCreditResponse.requests);
          setActiveAttemptId(attemptResponse.attempts[0]?.id ?? null);
        }
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setStatus(error instanceof Error ? error.message : "Unable to load payment status.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const availableCredit =
    user === null ? 0 : Math.max(user.current_credit_usd - user.credit_floor_usd, 0);
  const activeAttempt = paymentAttempts.find((attempt) => attempt.id === activeAttemptId) ?? paymentAttempts[0] ?? null;

  async function createAttempt(): Promise<void> {
    const amount = Number(paymentAmount);
    if (!Number.isFinite(amount) || amount <= 0) {
      setStatus("Enter a payment amount.");
      return;
    }
    try {
      const attempt = await createPayPalPaymentAttempt({ expected_amount_usd: amount });
      setPaymentAttempts((current) => [attempt, ...current]);
      setActiveAttemptId(attempt.id);
      setStatus("Payment reference created. Send PayPal payment with this reference, then upload the receipt.");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Unable to create payment reference.");
    }
  }

  async function uploadReceipt(): Promise<void> {
    if (!activeAttempt || !selectedReceipt) {
      setStatus("Choose a payment reference and receipt file first.");
      return;
    }
    try {
      const attempt = await uploadPayPalReceipt(activeAttempt.id, selectedReceipt);
      setPaymentAttempts((current) => current.map((candidate) => (candidate.id === attempt.id ? attempt : candidate)));
      setSelectedReceipt(null);
      setStatus(attempt.review_reason ?? `Receipt reviewed: ${attempt.status}.`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Unable to upload receipt.");
    }
  }

  async function submitFreeCreditRequest(): Promise<void> {
    const requestedAmount = Number(freeCreditAmount);
    if (!freeCreditReason.trim()) {
      setStatus("Add a short note for the credit request.");
      return;
    }
    if (!Number.isFinite(requestedAmount) || requestedAmount <= 0) {
      setStatus("Enter a positive free-credit amount.");
      return;
    }
    try {
      const request = await createFreeCreditRequest({
        requested_amount_usd: requestedAmount,
        source: "general",
        reason: freeCreditReason.trim(),
      });
      setFreeCreditRequests((current) => [request, ...current]);
      setFreeCreditReason("");
      setStatus(request.decision_note ?? `Free-credit request is ${request.status.replaceAll("_", " ")}.`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Unable to request free credit.");
    }
  }

  return (
    <section className="admin-workspace-panel account-workspace-panel" aria-label="Account and payment settings">
      <div className="account-settings-grid">
        <section className="account-settings-section" aria-label="Account">
          <p className="eyebrow">Account</p>
          <h2>{user?.display_name ?? "Signed-in user"}</h2>
          <dl>
            <div>
              <dt>Email</dt>
              <dd>{user?.primary_email ?? "Not provided"}</dd>
            </div>
            <div>
              <dt>Status</dt>
              <dd>{user?.active ? "Active" : "Pending activation"}</dd>
            </div>
            <div>
              <dt>Role</dt>
              <dd>{user?.role ?? "User"}</dd>
            </div>
          </dl>
        </section>
        <section className="account-settings-section" aria-label="Credits">
          <p className="eyebrow">Credits</p>
          <h2>{formatUsd(availableCredit)}</h2>
          <dl>
            <div>
              <dt>Current balance</dt>
              <dd>{formatUsd(user?.current_credit_usd ?? 0)}</dd>
            </div>
            <div>
              <dt>Credit floor</dt>
              <dd>{formatUsd(user?.credit_floor_usd ?? 0)}</dd>
            </div>
          </dl>
        </section>
        <section className="account-settings-section" aria-label="Payments">
          <p className="eyebrow">Payments</p>
          <h2>{paymentStatus?.receipt_upload_enabled ? "PayPal receipt credit" : "Payments unavailable"}</h2>
          <dl>
            <div>
              <dt>Provider</dt>
              <dd>{paymentStatus?.provider ?? "Loading"}</dd>
            </div>
            {paymentStatus?.paypal_recipient_email ? (
              <div>
                <dt>Send to</dt>
                <dd>{paymentStatus.paypal_recipient_email}</dd>
              </div>
            ) : null}
            <div>
              <dt>Details</dt>
              <dd>{status ?? paymentStatus?.reason ?? "Payment status is current."}</dd>
            </div>
          </dl>
          {paymentStatus?.receipt_upload_enabled ? (
            <div className="payment-receipt-flow">
              <label>
                Amount
                <input
                  inputMode="decimal"
                  onChange={(event) => setPaymentAmount(event.currentTarget.value)}
                  value={paymentAmount}
                />
              </label>
              <button type="button" className="secondary-button" onClick={() => void createAttempt()}>
                New reference
              </button>
              {activeAttempt ? (
                <div className="payment-reference-box">
                  <strong>{activeAttempt.reference_code}</strong>
                  <span>
                    {formatUsd(activeAttempt.expected_amount_usd)} {activeAttempt.expected_currency} / {activeAttempt.status.replaceAll("_", " ")}
                  </span>
                  {paymentStatus.paypal_payment_url ? (
                    <a href={paymentStatus.paypal_payment_url} target="_blank" rel="noreferrer">
                      Open PayPal
                    </a>
                  ) : null}
                </div>
              ) : null}
              <label>
                Receipt or invoice
                <input
                  type="file"
                  accept=".txt,.pdf,.eml,.html,.htm,text/plain,application/pdf,text/html,message/rfc822"
                  onChange={(event) => setSelectedReceipt(event.currentTarget.files?.[0] ?? null)}
                />
              </label>
              <button
                type="button"
                className="secondary-button"
                disabled={!activeAttempt || !selectedReceipt}
                onClick={() => void uploadReceipt()}
              >
                Upload receipt
              </button>
            </div>
          ) : null}
        </section>
        <section className="account-settings-section" aria-label="Free credit requests">
          <p className="eyebrow">Access</p>
          <h2>Request free credit</h2>
          <dl>
            <div>
              <dt>Latest request</dt>
              <dd>{freeCreditRequests[0]?.status.replaceAll("_", " ") ?? "None"}</dd>
            </div>
            <div>
              <dt>Decision</dt>
              <dd>{freeCreditRequests[0]?.decision_note ?? "Requests are reviewed by an admin."}</dd>
            </div>
          </dl>
          <div className="payment-receipt-flow">
            <label>
              Amount
              <input
                inputMode="decimal"
                onChange={(event) => setFreeCreditAmount(event.currentTarget.value)}
                value={freeCreditAmount}
              />
            </label>
            <label>
              Request note
              <textarea
                onChange={(event) => setFreeCreditReason(event.currentTarget.value)}
                placeholder="What are you trying to test or build?"
                value={freeCreditReason}
              />
            </label>
            <button type="button" className="secondary-button" onClick={() => void submitFreeCreditRequest()}>
              Request credit
            </button>
          </div>
        </section>
      </div>
    </section>
  );
}
