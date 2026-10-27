// XELTO fork: sign-in gate for multi-user (server) mode.
//
// Asks the backend who is signed in. A backend without multi-user mode answers
// 404 (the endpoint does not exist), and the app renders exactly as upstream.
// Signed out, the browser goes to Keycloak through the backend; signed in, the
// user is provided to the rest of the fork's UI.
import { useEffect, useState, type ReactNode } from 'react';
import { apiPath } from '@/lib/api/client';
import { useXt } from './i18n';
import {
  ME_PATH,
  XeltoUserContext,
  signInUrl,
  switchStoredUser,
  type XeltoUser,
} from './session';

type Me = { enabled: true; authenticated: false } | ({ enabled: true; authenticated: true } & XeltoUser);

type GateState =
  | { kind: 'checking' }
  | { kind: 'off' }
  | { kind: 'redirecting' }
  | { kind: 'signed-in'; user: XeltoUser };

export interface OidcGateProps {
  children: ReactNode;
  /** Only the web build can run against a multi-user server. */
  enabled?: boolean;
  fetchMe?: () => Promise<Response>;
  redirect?: (url: string) => void;
  storage?: () => Storage | null;
}

const fetchMeDefault = () => fetch(apiPath(ME_PATH), { credentials: 'include' });
const redirectDefault = (url: string) => window.location.assign(url);

function browserStorage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

export function XeltoOidcGate({
  children,
  enabled = __WEB_DEPLOYMENT__,
  fetchMe = fetchMeDefault,
  redirect = redirectDefault,
  storage = browserStorage,
}: OidcGateProps) {
  const xt = useXt();
  const [state, setState] = useState<GateState>(enabled ? { kind: 'checking' } : { kind: 'off' });

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    const toSignIn = () => {
      setState({ kind: 'redirecting' });
      redirect(signInUrl(window.location));
    };
    fetchMe()
      .then(async (response) => {
        const me: Me | null = response.ok ? await response.json().catch(() => null) : null;
        if (cancelled) return;
        if (!me?.enabled) {
          setState({ kind: 'off' });
          return;
        }
        if (!me.authenticated) {
          toSignIn();
          return;
        }
        const store = storage();
        if (store) {
          try {
            switchStoredUser(store, me.sub);
          } catch {
            // Blocked storage: nothing of a previous user to clear either.
          }
        }
        setState({ kind: 'signed-in', user: me });
      })
      .catch(() => {
        // Backend unreachable: upstream's own gates explain that.
        if (!cancelled) setState({ kind: 'off' });
      });
    return () => {
      cancelled = true;
    };
  }, [enabled, fetchMe, redirect, storage]);

  useEffect(() => {
    if (state.kind !== 'signed-in') return;
    // A 401 after sign-in means the session ended (expired, signed out
    // elsewhere, or disabled in Keycloak): sign in again.
    const expired = () => {
      setState({ kind: 'redirecting' });
      redirect(signInUrl(window.location));
    };
    window.addEventListener('ov:auth-required', expired);
    return () => window.removeEventListener('ov:auth-required', expired);
  }, [state.kind, redirect]);

  if (state.kind === 'checking' || state.kind === 'redirecting') {
    return (
      <div
        role="status"
        className="flex h-full min-h-screen items-center justify-center bg-background text-sm text-muted-foreground"
      >
        {xt('gate.signing_in')}
      </div>
    );
  }
  if (state.kind === 'off') return <>{children}</>;
  return <XeltoUserContext.Provider value={state.user}>{children}</XeltoUserContext.Provider>;
}
