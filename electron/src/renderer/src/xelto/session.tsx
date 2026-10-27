// XELTO fork: the signed-in VoiceStudio user in multi-user (server) mode.
import { createContext, useContext } from 'react';

export interface XeltoUser {
  sub: string;
  username: string;
  name: string;
  email: string;
  roles: string[];
  is_admin: boolean;
}

/** null when multi-user mode is off (desktop, or a single-user server). */
export const XeltoUserContext = createContext<XeltoUser | null>(null);

export function useXeltoUser(): XeltoUser | null {
  return useContext(XeltoUserContext);
}

/** True for a signed-in user without the admin role: admin-only UI is hidden. */
export function useXeltoRestricted(): boolean {
  const user = useXeltoUser();
  return user !== null && !user.is_admin;
}

export const LAST_SUB_KEY = 'xelto_last_sub';
export const SIGN_IN_PATH = '/api/auth/oidc/login';
export const ME_PATH = '/api/auth/oidc/me';
export const LOGOUT_PATH = '/api/auth/oidc/logout';

export function signInUrl(location: Pick<Location, 'pathname' | 'search' | 'hash'>): string {
  const next = `${location.pathname}${location.search}${location.hash}`;
  return `${SIGN_IN_PATH}?next=${encodeURIComponent(next)}`;
}

/**
 * Forget the previous user's browser state when someone else signs in on the
 * same browser. Upstream keeps drafts and preferences in localStorage; keys of
 * the fork itself (`xelto_*`) survive.
 */
export function switchStoredUser(storage: Storage, sub: string): boolean {
  const previous = storage.getItem(LAST_SUB_KEY);
  let cleared = false;
  if (previous !== null && previous !== sub) {
    const keys: string[] = [];
    for (let index = 0; index < storage.length; index += 1) {
      const key = storage.key(index);
      if (key !== null && !key.startsWith('xelto_')) keys.push(key);
    }
    for (const key of keys) storage.removeItem(key);
    cleared = true;
  }
  storage.setItem(LAST_SUB_KEY, sub);
  return cleared;
}
