import { act, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import '@/i18n';
import { XeltoOidcGate } from './oidc-gate';
import {
  KNOWN_DATABASES,
  LAST_SUB_KEY,
  clearBrowserStores,
  switchStoredUser,
  useXeltoUser,
} from './session';

const ALICE = {
  enabled: true,
  authenticated: true,
  sub: 'sub-alice',
  username: 'alice',
  name: 'Alice',
  email: 'alice@example.test',
  roles: ['voicestudio-user'],
  is_admin: false,
};

function json(body: unknown, status = 200) {
  return () => Promise.resolve(new Response(JSON.stringify(body), { status }));
}

function Who() {
  const user = useXeltoUser();
  return <p>{user ? `user:${user.username}` : 'no-user'}</p>;
}

function memoryStorage(initial: Record<string, string> = {}): Storage {
  const data = new Map(Object.entries(initial));
  return {
    get length() {
      return data.size;
    },
    clear: () => data.clear(),
    getItem: (key) => data.get(key) ?? null,
    key: (index) => [...data.keys()][index] ?? null,
    removeItem: (key) => void data.delete(key),
    setItem: (key, value) => void data.set(key, String(value)),
  };
}

describe('XeltoOidcGate', () => {
  let redirect: ReturnType<typeof vi.fn<(url: string) => void>>;
  beforeEach(() => {
    redirect = vi.fn<(url: string) => void>();
    window.history.replaceState(null, '', '/dub?job=7#lane');
  });

  it('renders the app untouched when disabled (desktop build)', () => {
    const fetchMe = vi.fn();
    render(
      <XeltoOidcGate enabled={false} fetchMe={fetchMe} redirect={redirect}>
        <Who />
      </XeltoOidcGate>,
    );
    expect(screen.getByText('no-user')).toBeInTheDocument();
    expect(fetchMe).not.toHaveBeenCalled();
  });

  it('renders the app when the backend has no multi-user mode (404)', async () => {
    render(
      <XeltoOidcGate enabled fetchMe={json({ detail: 'Not Found' }, 404)} redirect={redirect}>
        <Who />
      </XeltoOidcGate>,
    );
    expect(await screen.findByText('no-user')).toBeInTheDocument();
    expect(redirect).not.toHaveBeenCalled();
  });

  it('holds the app and sends a signed-out browser to sign-in, keeping the place', async () => {
    render(
      <XeltoOidcGate
        enabled
        fetchMe={json({ enabled: true, authenticated: false })}
        redirect={redirect}
      >
        <Who />
      </XeltoOidcGate>,
    );
    await waitFor(() =>
      expect(redirect).toHaveBeenCalledWith(
        '/api/auth/oidc/login?next=' + encodeURIComponent('/dub?job=7#lane'),
      ),
    );
    expect(screen.queryByText('no-user')).not.toBeInTheDocument();
    expect(screen.getByRole('status')).toBeInTheDocument();
  });

  it('provides the signed-in user', async () => {
    render(
      <XeltoOidcGate
        enabled
        fetchMe={json(ALICE)}
        redirect={redirect}
        storage={() => memoryStorage()}
      >
        <Who />
      </XeltoOidcGate>,
    );
    expect(await screen.findByText('user:alice')).toBeInTheDocument();
  });

  it('signs in again when the session ends', async () => {
    render(
      <XeltoOidcGate
        enabled
        fetchMe={json(ALICE)}
        redirect={redirect}
        storage={() => memoryStorage()}
      >
        <Who />
      </XeltoOidcGate>,
    );
    await screen.findByText('user:alice');
    act(() => {
      window.dispatchEvent(new CustomEvent('ov:auth-required', { detail: { mode: 'pin' } }));
    });
    expect(redirect).toHaveBeenCalledTimes(1);
    expect(screen.queryByText('user:alice')).not.toBeInTheDocument();
  });

  it('renders the app when the backend is unreachable', async () => {
    render(
      <XeltoOidcGate enabled fetchMe={() => Promise.reject(new TypeError('offline'))}>
        <Who />
      </XeltoOidcGate>,
    );
    expect(await screen.findByText('no-user')).toBeInTheDocument();
  });
});

describe('switchStoredUser', () => {
  it('keeps everything for the same user', () => {
    const storage = memoryStorage({ [LAST_SUB_KEY]: 'sub-alice', draft: 'hi' });
    expect(switchStoredUser(storage, 'sub-alice')).toBe(false);
    expect(storage.getItem('draft')).toBe('hi');
  });

  it("clears the previous user's browser state, keeping fork keys", () => {
    const storage = memoryStorage({
      [LAST_SUB_KEY]: 'sub-alice',
      draft: 'alice text',
      'ov:clone-settings': '{}',
      xelto_other: 'kept',
    });
    expect(switchStoredUser(storage, 'sub-bob')).toBe(true);
    expect(storage.getItem('draft')).toBeNull();
    expect(storage.getItem('ov:clone-settings')).toBeNull();
    expect(storage.getItem('xelto_other')).toBe('kept');
    expect(storage.getItem(LAST_SUB_KEY)).toBe('sub-bob');
  });

  it('keeps state from before multi-user mode on first sign-in', () => {
    const storage = memoryStorage({ theme: 'dark' });
    expect(switchStoredUser(storage, 'sub-alice')).toBe(false);
    expect(storage.getItem('theme')).toBe('dark');
  });
});

function fakeDatabases(listed: string[] | null) {
  const deleted: string[] = [];
  const factory = {
    deleteDatabase(name: string) {
      deleted.push(name);
      const request = {} as IDBOpenDBRequest;
      queueMicrotask(() => request.onsuccess?.(new Event('success')));
      return request;
    },
    ...(listed ? { databases: () => Promise.resolve(listed.map((name) => ({ name, version: 1 }))) } : {}),
  } as unknown as IDBFactory;
  return { factory, deleted };
}

describe('clearBrowserStores', () => {
  it('clears sessionStorage and deletes every database', async () => {
    const session = memoryStorage({ ov_pin: '1234' });
    const { factory, deleted } = fakeDatabases(['voicestudio.workflow-runs', 'other-db']);
    await clearBrowserStores({ session, databases: factory });
    expect(session.length).toBe(0);
    expect(deleted.sort()).toEqual(['other-db', 'voicestudio.workflow-runs']);
  });

  it('deletes the known databases where they cannot be listed', async () => {
    const { factory, deleted } = fakeDatabases(null);
    await clearBrowserStores({ session: null, databases: factory });
    expect(deleted).toEqual(KNOWN_DATABASES);
  });
});

describe('XeltoOidcGate and a previous user', () => {
  it("clears the previous user's databases before rendering the app", async () => {
    const { factory, deleted } = fakeDatabases(['voicestudio.workflow-runs']);
    render(
      <XeltoOidcGate
        enabled
        fetchMe={json(ALICE)}
        redirect={vi.fn()}
        storage={() => memoryStorage({ [LAST_SUB_KEY]: 'sub-bob', draft: 'his' })}
        otherStores={() => ({ session: null, databases: factory })}
      >
        <Who />
      </XeltoOidcGate>,
    );
    expect(await screen.findByText('user:alice')).toBeInTheDocument();
    expect(deleted).toEqual(['voicestudio.workflow-runs']);
  });

  it('keeps the databases of the same user', async () => {
    const { factory, deleted } = fakeDatabases(['voicestudio.workflow-runs']);
    render(
      <XeltoOidcGate
        enabled
        fetchMe={json(ALICE)}
        redirect={vi.fn()}
        storage={() => memoryStorage({ [LAST_SUB_KEY]: 'sub-alice' })}
        otherStores={() => ({ session: null, databases: factory })}
      >
        <Who />
      </XeltoOidcGate>,
    );
    expect(await screen.findByText('user:alice')).toBeInTheDocument();
    await waitFor(() => expect(deleted).toEqual([]));
  });
});
