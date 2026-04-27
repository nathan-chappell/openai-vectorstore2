import { useMemo } from "react";

import { AdminPortfolioPanel } from "../../../vendor/ai-portfolio-admin/frontend";
import type {
  AdminPortfolioPanelCallbacks,
  AdminUserSummary as SharedAdminUserSummary,
  CreditGrantRecord,
  ManualCreditGrantRequest,
} from "../../../vendor/ai-portfolio-admin/frontend";
import { grantAdminCredit, listAdminUsers, setAdminUserActive } from "../lib/api";
import type {
  AdminGrantCreditResponse,
  AdminSetUserActiveResponse,
  AdminUserSummary as LocalAdminUserSummary,
  AuthUser,
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
    };
  }, []);

  if (user?.role !== "admin") {
    return null;
  }

  return (
    <section className="admin-workspace-panel" aria-label="Admin users and credits">
      <AdminPortfolioPanel callbacks={callbacks} />
    </section>
  );
}
