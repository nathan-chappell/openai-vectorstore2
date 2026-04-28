import { ClerkProvider, SignInButton, useAuth, useClerk } from "@clerk/react";
import { StrictMode, useEffect } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import { setBearerTokenGetter, setUnauthorizedHandler } from "./lib/api";
import "./styles.css";

const clerkPublishableKey = import.meta.env.VITE_CLERK_PUBLISHABLE_KEY as string | undefined;

function ClerkTokenBridge() {
  const { getToken, isLoaded, isSignedIn } = useAuth();
  const { signOut } = useClerk();

  useEffect(() => {
    setUnauthorizedHandler(() => {
      void signOut();
    });
    return () => setUnauthorizedHandler(null);
  }, [signOut]);

  useEffect(() => {
    if (!isLoaded || !isSignedIn) {
      setBearerTokenGetter(null);
      return () => setBearerTokenGetter(null);
    }
    setBearerTokenGetter(async () => {
      return await getToken();
    });
    return () => setBearerTokenGetter(null);
  }, [getToken, isLoaded, isSignedIn]);

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
    {clerkPublishableKey ? (
      <ClerkProvider publishableKey={clerkPublishableKey}>
        <ClerkTokenBridge />
      </ClerkProvider>
    ) : (
      <LocalDevApp />
    )}
  </StrictMode>,
);
