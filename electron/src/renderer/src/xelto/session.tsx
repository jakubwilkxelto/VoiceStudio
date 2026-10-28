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

/** IndexedDB databases upstream creates, for browsers without `indexedDB.databases()`. */
export const KNOWN_DATABASES = ['voicestudio.workflow-runs'];

export interface BrowserStores {
  session: Storage | null;
  databases: IDBFactory | null;
}

/**
 * The rest of a previous user's browser state, after `switchStoredUser` found
 * a different user: sessionStorage and every IndexedDB database (upstream keeps
 * workflow runs, rendered audio included, in one). Runs before the app renders,
 * so nothing holds a database open; a blocked or failed delete is skipped.
 */
export async function clearBrowserStores({ session, databases }: BrowserStores): Promise<void> {
  try {
    session?.clear();
  } catch {
    // Blocked storage holds nothing to clear.
  }
  if (!databases) return;
  const names = new Set(KNOWN_DATABASES);
  try {
    if (typeof databases.databases === 'function') {
      for (const info of await databases.databases()) if (info.name) names.add(info.name);
    }
  } catch {
    // Listing is optional; the known names are still deleted.
  }
  await Promise.all(
    [...names].map(
      (name) =>
        new Promise<void>((resolve) => {
          try {
            const request = databases.deleteDatabase(name);
            request.onsuccess = request.onerror = request.onblocked = () => resolve();
          } catch {
            resolve();
          }
        }),
    ),
  );
}
