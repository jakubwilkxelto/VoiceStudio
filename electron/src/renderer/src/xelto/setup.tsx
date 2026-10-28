// XELTO fork: first-run setup once per server, by the admin, in multi-user mode.
//
// Upstream remembers a finished setup in this browser's localStorage, which the
// fork clears when someone else signs in, and every step writes server-wide
// settings only the admin may change. So in multi-user mode the server decides:
// setup is done once the models are installed and the analytics question is
// answered. Until then the admin gets the wizard and everyone else a waiting
// screen; afterwards nobody sees it.
import { useQuery } from '@tanstack/react-query';
import { LogOutIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { apiJson } from '@/lib/api/client';
import { brandIcon } from '@/lib/brand';
import { setupWasCompleted } from '@/lib/setup-progress';
import { useXt } from './i18n';
import { useXeltoUser } from './session';
import { signOut } from './user-menu';

/** How often a waiting user checks whether the admin has finished. */
export const PENDING_RECHECK_MS = 30_000;

interface SetupStatus {
  models_ready: boolean;
}

interface AnalyticsState {
  available: boolean;
  prompted: boolean;
  opted_in: boolean;
}

export interface XeltoSetup {
  /**
   * Whether setup counts as done: `null` with multi-user mode off (upstream's
   * own check applies), `undefined` while the server is still being asked.
   */
  completed: boolean | null | undefined;
  /** A user waits for the admin to finish setting the server up. */
  pending: boolean;
}

export function useXeltoSetup(): XeltoSetup {
  const user = useXeltoUser();
  const active = user !== null;
  const waiting = active && !user.is_admin;
  // The same query SetupGate runs; a waiting user's copy also re-checks.
  const status = useQuery({
    queryKey: ['setup-status'],
    queryFn: ({ signal }) => apiJson<SetupStatus>('/setup/status', { signal }),
    enabled: active,
    refetchInterval: (query) => (waiting && !query.state.data?.models_ready ? PENDING_RECHECK_MS : false),
  });
  const analytics = useQuery({
    queryKey: ['xelto-setup-analytics'],
    queryFn: ({ signal }) => apiJson<AnalyticsState>('/api/settings/analytics', { signal }),
    enabled: active,
    refetchInterval: (query) => {
      const data = query.state.data;
      return waiting && data && data.available && !data.prompted && !data.opted_in
        ? PENDING_RECHECK_MS
        : false;
    },
  });
  if (!active) return { completed: null, pending: false };
  if (!status.data || !analytics.data) {
    // A failed check never traps anyone: the admin falls back to upstream's
    // own state, a user is let in (generation errors explain what is missing).
    if (status.isError || analytics.isError) return { completed: user.is_admin ? null : true, pending: false };
    return { completed: undefined, pending: false };
  }
  const answered = !analytics.data.available || analytics.data.prompted || analytics.data.opted_in;
  const ready = status.data.models_ready && answered;
  if (user.is_admin) return { completed: ready || setupWasCompleted(), pending: false };
  return { completed: true, pending: !ready };
}

export function XeltoSetupPending() {
  const xt = useXt();
  return (
    <div
      role="status"
      className="flex h-full min-w-0 flex-1 flex-col items-center justify-center gap-4 p-6 text-center"
    >
      <img src={brandIcon} alt="" className="size-9" />
      <h1 className="text-lg font-semibold tracking-tight">{xt('setup.pending_title')}</h1>
      <p className="max-w-md text-sm text-muted-foreground">{xt('setup.pending_body')}</p>
      <Button variant="ghost" onClick={() => void signOut()}>
        <LogOutIcon className="size-4" aria-hidden="true" />
        {xt('menu.sign_out')}
      </Button>
    </div>
  );
}
