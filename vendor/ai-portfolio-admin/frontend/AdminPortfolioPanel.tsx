import { useEffect, useMemo, useState } from "react";
import type { FormEvent } from "react";

import type {
  AdminPortfolioPanelCallbacks,
  AdminUserSummary,
  FreeCreditRequestRecord,
  FreeCreditRequestStatus,
  PaymentAttemptRecord,
  PaymentAttemptStatus,
} from "./types";

type AdminPortfolioPanelProps = {
  callbacks: AdminPortfolioPanelCallbacks;
  pageSize?: number;
};

function formatUsd(value: number): string {
  return `$${value.toFixed(2)}`;
}

function availableCredit(user: AdminUserSummary): string {
  if (user.role === "admin") {
    return "N/A";
  }
  return formatUsd(Math.max(user.current_credit_usd - user.credit_floor_usd, 0));
}

function formatDate(value: string | number | null): string {
  if (!value) {
    return "Never";
  }
  return new Date(value).toLocaleDateString();
}

function formatStatus(value: string): string {
  return value.replaceAll("_", " ");
}

export function AdminPortfolioPanel({ callbacks, pageSize = 10 }: AdminPortfolioPanelProps) {
  const [users, setUsers] = useState<AdminUserSummary[]>([]);
  const [freeCreditRequests, setFreeCreditRequests] = useState<FreeCreditRequestRecord[]>([]);
  const [paymentAttempts, setPaymentAttempts] = useState<PaymentAttemptRecord[]>([]);
  const [requestStatus, setRequestStatus] = useState<FreeCreditRequestStatus>("pending");
  const [paymentStatus, setPaymentStatus] = useState<PaymentAttemptStatus>("manual_review_required");
  const [query, setQuery] = useState("");
  const [draftQuery, setDraftQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [selectedUserId, setSelectedUserId] = useState("");
  const [creditAmount, setCreditAmount] = useState("5");
  const [note, setNote] = useState("");
  const [status, setStatus] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const selectedUser = useMemo(
    () => users.find((candidate) => candidate.user_id === selectedUserId) ?? null,
    [selectedUserId, users],
  );
  const freeCreditEnabled = Boolean(callbacks.listFreeCreditRequests && callbacks.decideFreeCreditRequest);
  const paymentReviewEnabled = Boolean(callbacks.listPaymentAttempts && callbacks.decidePaymentAttempt);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setStatus(null);
    void Promise.all([
      callbacks.searchUsers(query, offset, pageSize),
      callbacks.listFreeCreditRequests?.(requestStatus) ?? Promise.resolve([]),
      callbacks.listPaymentAttempts?.(paymentStatus) ?? Promise.resolve([]),
    ])
      .then(([userResponse, requestResponse, paymentResponse]) => {
        if (cancelled) {
          return;
        }
        setUsers(userResponse.items);
        setHasMore(userResponse.has_more);
        setFreeCreditRequests(requestResponse);
        setPaymentAttempts(paymentResponse);
        setSelectedUserId((current) => {
          if (current && userResponse.items.some((candidate) => candidate.user_id === current)) {
            return current;
          }
          return userResponse.items[0]?.user_id ?? "";
        });
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setStatus(error instanceof Error ? error.message : "Unable to load admin data.");
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [callbacks, offset, pageSize, paymentStatus, query, requestStatus]);

  async function setActive(user: AdminUserSummary, active: boolean): Promise<void> {
    setSubmitting(true);
    setStatus(null);
    try {
      const updated = await callbacks.setUserActive(user.user_id, active);
      setUsers((current) => current.map((candidate) => (candidate.user_id === updated.user_id ? updated : candidate)));
      setStatus(`${updated.email ?? updated.user_id} is now ${updated.is_active ? "active" : "inactive"}.`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Unable to update activation.");
    } finally {
      setSubmitting(false);
    }
  }

  async function submitGrant(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    const amount = Number(creditAmount);
    if (!selectedUserId || !note.trim()) {
      setStatus("Select a user and add an audit note.");
      return;
    }
    if (!Number.isFinite(amount) || amount <= 0) {
      setStatus("Enter a positive credit amount.");
      return;
    }
    setSubmitting(true);
    setStatus(null);
    try {
      const grant = await callbacks.grantCredit({
        user_id: selectedUserId,
        credit_amount_usd: amount,
        note: note.trim(),
        source: "admin_manual",
      });
      setUsers((current) =>
        current.map((candidate) =>
          candidate.user_id === grant.user_id && grant.resulting_balance_usd !== null
            ? { ...candidate, current_credit_usd: grant.resulting_balance_usd }
            : candidate,
        ),
      );
      setNote("");
      setStatus(`Granted ${formatUsd(grant.credit_amount_usd)} to ${grant.user_id}.`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Unable to grant credit.");
    } finally {
      setSubmitting(false);
    }
  }

  async function decideRequest(
    request: FreeCreditRequestRecord,
    nextStatus: "approved" | "rejected" | "manual_review_required",
  ): Promise<void> {
    if (!callbacks.decideFreeCreditRequest) {
      return;
    }
    setSubmitting(true);
    setStatus(null);
    try {
      const updated = await callbacks.decideFreeCreditRequest({
        request_id: request.id,
        status: nextStatus,
        credit_amount_usd: nextStatus === "approved" ? (request.requested_amount_usd ?? 5) : null,
        decision_note: nextStatus === "approved" ? "Approved from admin panel." : "Reviewed from admin panel.",
      });
      setFreeCreditRequests((current) => current.map((candidate) => (candidate.id === updated.id ? updated : candidate)));
      setStatus(`${updated.user_id} free-credit request is now ${updated.status}.`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Unable to review free-credit request.");
    } finally {
      setSubmitting(false);
    }
  }

  async function decidePayment(
    attempt: PaymentAttemptRecord,
    nextStatus: "confirmed_paid" | "rejected_payment" | "manual_review_required",
  ): Promise<void> {
    if (!callbacks.decidePaymentAttempt) {
      return;
    }
    setSubmitting(true);
    setStatus(null);
    try {
      const updated = await callbacks.decidePaymentAttempt({
        attempt_id: attempt.id,
        status: nextStatus,
        credit_amount_usd: nextStatus === "confirmed_paid" ? attempt.expected_amount_usd : null,
        provider_reference: attempt.provider_reference,
        decision_note: nextStatus === "confirmed_paid" ? "Confirmed from admin panel." : "Reviewed from admin panel.",
      });
      setPaymentAttempts((current) => current.map((candidate) => (candidate.id === updated.id ? updated : candidate)));
      setStatus(`${updated.user_id} payment attempt is now ${updated.status}.`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Unable to review payment attempt.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section aria-busy={loading || submitting} className="admin-portfolio-panel">
      <header className="admin-portfolio-panel__header">
        <div>
          <h2>Users</h2>
          <p>Search accounts, manage activation, and grant auditable credits.</p>
        </div>
        <form
          className="admin-portfolio-panel__toolbar"
          onSubmit={(event) => {
            event.preventDefault();
            setOffset(0);
            setQuery(draftQuery.trim());
          }}
        >
          <input onChange={(event) => setDraftQuery(event.target.value)} placeholder="Search users" value={draftQuery} />
          <button type="submit">Search</button>
        </form>
      </header>

      {status ? (
        <p className="admin-portfolio-panel__status" role="status">
          {status}
        </p>
      ) : null}
      {loading ? <p className="admin-portfolio-panel__loading">Loading...</p> : null}

      <div className="admin-portfolio-panel__table-wrap">
        <table className="admin-portfolio-panel__table">
          <thead>
            <tr>
              <th>User</th>
              <th>Role</th>
              <th>Active</th>
              <th>Credit</th>
              <th>Last seen</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {users.map((user) => (
              <tr
                className={`admin-portfolio-panel__user-row${user.user_id === selectedUserId ? " admin-portfolio-panel__user-row--selected" : ""}${
                  user.is_active ? "" : " admin-portfolio-panel__user-row--inactive"
                }`}
                key={user.user_id}
              >
                <td className="admin-portfolio-panel__identity-cell">
                  <strong>{user.display_name ?? user.email ?? user.user_id}</strong>
                  <span>{user.email ?? user.user_id}</span>
                </td>
                <td>
                  <span className="admin-portfolio-panel__pill">{user.role}</span>
                </td>
                <td>
                  <span
                    className={`admin-portfolio-panel__pill ${
                      user.is_active ? "admin-portfolio-panel__pill--positive" : "admin-portfolio-panel__pill--muted"
                    }`}
                  >
                    {user.is_active ? "Active" : "Inactive"}
                  </span>
                </td>
                <td className="admin-portfolio-panel__credit-cell">
                  <strong>{formatUsd(user.current_credit_usd)}</strong>
                  <span>{availableCredit(user)} usable</span>
                </td>
                <td>{formatDate(user.last_sign_in_at_ms)}</td>
                <td className="admin-portfolio-panel__actions-cell">
                  <button
                    aria-pressed={user.user_id === selectedUserId}
                    className="admin-portfolio-panel__button admin-portfolio-panel__button--secondary"
                    onClick={() => setSelectedUserId(user.user_id)}
                    type="button"
                  >
                    {user.user_id === selectedUserId ? "Selected" : "Select"}
                  </button>
                  <button
                    className={`admin-portfolio-panel__button ${
                      user.is_active ? "admin-portfolio-panel__button--danger" : "admin-portfolio-panel__button--positive"
                    }`}
                    disabled={submitting}
                    onClick={() => void setActive(user, !user.is_active)}
                    type="button"
                  >
                    {user.is_active ? "Deactivate" : "Activate"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <footer className="admin-portfolio-panel__pager">
        <button disabled={offset === 0} onClick={() => setOffset(Math.max(offset - pageSize, 0))} type="button">
          Previous
        </button>
        <button disabled={!hasMore} onClick={() => setOffset(offset + pageSize)} type="button">
          Next
        </button>
      </footer>

      <div className="admin-portfolio-panel__workbench">
        <form className="admin-portfolio-panel__grant" onSubmit={(event) => void submitGrant(event)}>
          <h3>Manual Credit Grant</h3>
          <p>{selectedUser ? selectedUser.email ?? selectedUser.user_id : "No user selected"}</p>
          <div className="admin-portfolio-panel__grant-fields">
            <label>
              Amount
              <input
                min="0.01"
                onChange={(event) => setCreditAmount(event.target.value)}
                step="0.01"
                type="number"
                value={creditAmount}
              />
            </label>
            <label>
              Audit note
              <textarea onChange={(event) => setNote(event.target.value)} placeholder="Reason, payment, or support context" value={note} />
            </label>
          </div>
          <button className="admin-portfolio-panel__button admin-portfolio-panel__button--primary" disabled={submitting} type="submit">
            Grant Credit
          </button>
        </form>

        <div className="admin-portfolio-panel__review-grid">
          {freeCreditEnabled ? (
            <section className="admin-portfolio-panel__review">
              <header>
                <h3>Free Credit Requests</h3>
                <select onChange={(event) => setRequestStatus(event.target.value as FreeCreditRequestStatus)} value={requestStatus}>
                  <option value="pending">Pending</option>
                  <option value="manual_review_required">Manual review</option>
                  <option value="approved">Approved</option>
                  <option value="rejected">Rejected</option>
                  <option value="expired">Expired</option>
                </select>
              </header>
              {freeCreditRequests.length ? (
                freeCreditRequests.map((request) => (
                  <article className="admin-portfolio-panel__review-item" key={request.id}>
                    <div>
                      <strong>{request.user_id}</strong>
                      <span className="admin-portfolio-panel__pill">{formatStatus(request.status)}</span>
                    </div>
                    <p>{request.reason}</p>
                    {request.linkedin_profile_url ? <p>{request.linkedin_profile_url}</p> : null}
                    <div className="admin-portfolio-panel__review-actions">
                      <button
                        className="admin-portfolio-panel__button admin-portfolio-panel__button--positive"
                        disabled={submitting}
                        onClick={() => void decideRequest(request, "approved")}
                        type="button"
                      >
                        Approve
                      </button>
                      <button
                        className="admin-portfolio-panel__button admin-portfolio-panel__button--danger"
                        disabled={submitting}
                        onClick={() => void decideRequest(request, "rejected")}
                        type="button"
                      >
                        Reject
                      </button>
                      <button
                        className="admin-portfolio-panel__button admin-portfolio-panel__button--secondary"
                        disabled={submitting}
                        onClick={() => void decideRequest(request, "manual_review_required")}
                        type="button"
                      >
                        Review
                      </button>
                    </div>
                  </article>
                ))
              ) : (
                <p className="admin-portfolio-panel__empty">No requests for this status.</p>
              )}
            </section>
          ) : null}

          {paymentReviewEnabled ? (
            <section className="admin-portfolio-panel__review">
              <header>
                <h3>Payment Attempts</h3>
                <select onChange={(event) => setPaymentStatus(event.target.value as PaymentAttemptStatus)} value={paymentStatus}>
                  <option value="pending_payment">Pending payment</option>
                  <option value="temporarily_approved">Temporary access</option>
                  <option value="manual_review_required">Manual review</option>
                  <option value="confirmed_paid">Confirmed paid</option>
                  <option value="rejected_payment">Rejected</option>
                  <option value="expired_temporary_access">Expired temporary access</option>
                </select>
              </header>
              {paymentAttempts.length ? (
                paymentAttempts.map((attempt) => (
                  <article className="admin-portfolio-panel__review-item" key={attempt.id}>
                    <div>
                      <strong>{attempt.user_id}</strong>
                      <span className="admin-portfolio-panel__pill">{formatStatus(attempt.status)}</span>
                    </div>
                    <p>
                      {attempt.provider} {formatUsd(attempt.expected_amount_usd)} {attempt.expected_currency} / {attempt.reference_code}
                    </p>
                    <div className="admin-portfolio-panel__review-actions">
                      <button
                        className="admin-portfolio-panel__button admin-portfolio-panel__button--positive"
                        disabled={submitting}
                        onClick={() => void decidePayment(attempt, "confirmed_paid")}
                        type="button"
                      >
                        Confirm
                      </button>
                      <button
                        className="admin-portfolio-panel__button admin-portfolio-panel__button--danger"
                        disabled={submitting}
                        onClick={() => void decidePayment(attempt, "rejected_payment")}
                        type="button"
                      >
                        Reject
                      </button>
                      <button
                        className="admin-portfolio-panel__button admin-portfolio-panel__button--secondary"
                        disabled={submitting}
                        onClick={() => void decidePayment(attempt, "manual_review_required")}
                        type="button"
                      >
                        Review
                      </button>
                    </div>
                  </article>
                ))
              ) : (
                <p className="admin-portfolio-panel__empty">No payment attempts for this status.</p>
              )}
            </section>
          ) : null}
        </div>
      </div>
    </section>
  );
}
