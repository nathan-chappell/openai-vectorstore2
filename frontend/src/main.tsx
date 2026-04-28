import { ClerkProvider, SignInButton, useAuth, useClerk } from "@clerk/react";
import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import { setBearerTokenGetter, setChatKitDomainKey, setUnauthorizedHandler } from "./lib/api";
import "./styles.css";

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "/api";
const buildClerkPublishableKey = import.meta.env.VITE_CLERK_PUBLISHABLE_KEY as string | undefined;
const buildChatKitDomainKey = import.meta.env.VITE_CHATKIT_DOMAIN_KEY as string | undefined;

type ClientConfig = {
  chatkit_domain_key: string | null;
  clerk_publishable_key: string | null;
};

function RuntimeApp() {
  const [clerkPublishableKey, setClerkPublishableKey] = useState<string | null | undefined>(undefined);

  useEffect(() => {
    let cancelled = false;
    void fetch(`${apiBaseUrl}/client-config`)
      .then(async (response) => {
        if (!response.ok) {
          throw new Error(`Client config failed with ${response.status}`);
        }
        return (await response.json()) as ClientConfig;
      })
      .then((config) => {
        if (!cancelled) {
          setChatKitDomainKey(config.chatkit_domain_key || buildChatKitDomainKey || null);
          setClerkPublishableKey(config.clerk_publishable_key || buildClerkPublishableKey || null);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setChatKitDomainKey(buildChatKitDomainKey || null);
          setClerkPublishableKey(buildClerkPublishableKey || null);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (clerkPublishableKey === undefined) {
    return (
      <main className="auth-shell">
        <section className="auth-panel" aria-labelledby="auth-title">
          <div className="app-identity auth-identity">
            <strong>AI Files</strong>
            <span>Opening</span>
          </div>
          <h1 id="auth-title">Opening workspace</h1>
          <p>Loading app settings.</p>
        </section>
      </main>
    );
  }

  return clerkPublishableKey ? (
    <ClerkProvider publishableKey={clerkPublishableKey}>
      <ClerkTokenBridge />
    </ClerkProvider>
  ) : (
    <LocalDevApp />
  );
}

function ClerkTokenBridge() {
  const { getToken, isLoaded, isSignedIn } = useAuth();
  const { signOut } = useClerk();
  const [tokenReady, setTokenReady] = useState(false);

  useEffect(() => {
    setUnauthorizedHandler(() => {
      void signOut();
    });
    return () => setUnauthorizedHandler(null);
  }, [signOut]);

  useEffect(() => {
    if (!isLoaded || !isSignedIn) {
      setTokenReady(false);
      setBearerTokenGetter(null);
      return () => {
        setBearerTokenGetter(null);
        setTokenReady(false);
      };
    }
    let cancelled = false;
    setTokenReady(false);
    void getToken().then((token) => {
      if (cancelled) {
        return;
      }
      if (!token) {
        setBearerTokenGetter(null);
        void signOut();
        return;
      }
      setBearerTokenGetter(async () => {
        return await getToken();
      });
      setTokenReady(true);
    });
    return () => {
      cancelled = true;
      setBearerTokenGetter(null);
      setTokenReady(false);
    };
  }, [getToken, isLoaded, isSignedIn, signOut]);

  if (!isLoaded) {
    return (
      <main className="auth-shell">
        <section className="auth-panel" aria-labelledby="auth-title">
          <div className="app-identity auth-identity">
            <strong>AI Files</strong>
            <span>Clerk auth</span>
          </div>
          <h1 id="auth-title">Opening sign in</h1>
          <p>Checking your session.</p>
        </section>
      </main>
    );
  }

  if (!isSignedIn) {
    return (
      <main className="auth-shell">
        <section className="auth-panel" aria-labelledby="auth-title">
          <div className="app-identity auth-identity">
            <strong>AI Files</strong>
            <span>Clerk auth</span>
          </div>
          <h1 id="auth-title">Sign in to continue</h1>
          <p>Use your account to open the workspace.</p>
          <SignInButton mode="modal">
            <button type="button">Sign In</button>
          </SignInButton>
        </section>
      </main>
    );
  }

  if (!tokenReady) {
    return (
      <main className="auth-shell">
        <section className="auth-panel" aria-labelledby="auth-title">
          <div className="app-identity auth-identity">
            <strong>AI Files</strong>
            <span>Clerk auth</span>
          </div>
          <h1 id="auth-title">Opening workspace</h1>
          <p>Preparing your session.</p>
        </section>
      </main>
    );
  }

  return <App authMode="clerk" onSignOut={() => void signOut()} />;
}

function LocalDevApp() {
  useEffect(() => {
    setBearerTokenGetter(async () => "local-dev");
    return () => setBearerTokenGetter(null);
  }, []);

  return <App authMode="local-dev" />;
}

const root = createRoot(document.getElementById("root") as HTMLElement);

root.render(
  <StrictMode>
    <RuntimeApp />
  </StrictMode>,
);
