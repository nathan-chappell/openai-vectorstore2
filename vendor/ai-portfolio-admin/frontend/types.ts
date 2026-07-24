export type UserRole = "admin" | "user";
export type CreditGrantSource =
  | "admin_manual"
  | "free_credit_request"
  | "paypal_receipt"
  | "paypal_reversal"
  | "paypal_checkout"
  | "stripe_checkout"
  | "system";
export type FreeCreditSource = "general" | "linkedin_connection" | "beta_tester" | "manual_admin";
export type FreeCreditRequestStatus = "pending" | "approved" | "rejected" | "manual_review_required" | "expired";
export type PaymentAttemptStatus =
  | "pending_payment"
  | "temporarily_approved"
  | "confirmed_paid"
  | "rejected_payment"
  | "expired_temporary_access"
  | "manual_review_required";
export type PaymentProvider = "paypal" | "stripe" | "manual" | "none";

export type AdminUserSummary = {
  user_id: string;
  email: string | null;
  display_name: string | null;
  image_url: string | null;
  role: UserRole;
  is_active: boolean;
  current_credit_usd: number;
  credit_floor_usd: number;
  created_at_ms: number | null;
  last_sign_in_at_ms: number | null;
};

export type ManualCreditGrantRequest = {
  user_id: string;
  credit_amount_usd: number;
  note: string;
  source?: CreditGrantSource;
  request_id?: string | null;
  payment_reference?: string | null;
  idempotency_key?: string | null;
};

export type CreditGrantRecord = {
  id: string;
  user_id: string;
  admin_user_id: string | null;
  credit_amount_usd: number;
  source: CreditGrantSource;
  note: string | null;
  request_id: string | null;
  payment_provider: string | null;
  payment_reference: string | null;
  resulting_balance_usd: number | null;
  created_at: string;
};

export type FreeCreditRequestRecord = {
  id: string;
  user_id: string;
  requested_amount_usd: number | null;
  source: FreeCreditSource;
  reason: string;
  linkedin_profile_url: string | null;
  relationship_note: string | null;
  intended_use: string | null;
  evidence_verified: boolean;
  idempotency_key: string | null;
  status: FreeCreditRequestStatus;
  decided_amount_usd: number | null;
  decision_note: string | null;
  reviewer_user_id: string | null;
  credit_grant_id: string | null;
  created_at: string;
  decided_at: string | null;
};

export type FreeCreditDecisionPayload = {
  request_id: string;
  status: Extract<FreeCreditRequestStatus, "approved" | "rejected" | "manual_review_required">;
  credit_amount_usd?: number | null;
  decision_note: string;
};

export type PaymentAttemptRecord = {
  id: string;
  user_id: string;
  provider: PaymentProvider;
  expected_amount_usd: number;
  expected_currency: string;
  reference_code: string;
  status: PaymentAttemptStatus;
  temporary_access_expires_at: string | null;
  provider_reference: string | null;
  created_at: string;
};

export type PaymentAttemptDecisionPayload = {
  attempt_id: string;
  status: Extract<PaymentAttemptStatus, "confirmed_paid" | "rejected_payment" | "manual_review_required">;
  decision_note: string;
  credit_amount_usd?: number | null;
  provider_reference?: string | null;
};

export type AdminPortfolioPanelCallbacks = {
  searchUsers(query: string, offset: number, limit: number): Promise<{ items: AdminUserSummary[]; has_more: boolean }>;
  setUserActive(userId: string, active: boolean): Promise<AdminUserSummary>;
  grantCredit(payload: ManualCreditGrantRequest): Promise<CreditGrantRecord>;
  listFreeCreditRequests?: (status: FreeCreditRequestStatus) => Promise<FreeCreditRequestRecord[]>;
  decideFreeCreditRequest?: (payload: FreeCreditDecisionPayload) => Promise<FreeCreditRequestRecord>;
  listPaymentAttempts?: (status: PaymentAttemptStatus) => Promise<PaymentAttemptRecord[]>;
  decidePaymentAttempt?: (payload: PaymentAttemptDecisionPayload) => Promise<PaymentAttemptRecord>;
};
