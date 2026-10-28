import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import '@/i18n';
import { XeltoUserContext, type XeltoUser } from './session';
import { useXeltoSetup, XeltoSetupPending } from './setup';

const answers = vi.hoisted(() => ({ models_ready: true, prompted: true, failing: false }));

vi.mock('@/lib/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api/client')>()),
  apiJson: vi.fn(async (path: string) => {
    if (answers.failing) throw new Error('offline');
    if (path === '/setup/status') return { models_ready: answers.models_ready, missing: [] };
    if (path === '/api/settings/analytics') return { available: true, prompted: answers.prompted, opted_in: false };
    throw new Error(`unexpected ${path}`);
  }),
}));

const USER: XeltoUser = {
  sub: 'sub-bob',
  username: 'bob',
  name: 'Bob',
  email: 'bob@example.test',
  roles: ['voicestudio-user'],
  is_admin: false,
};
const ADMIN: XeltoUser = { ...USER, sub: 'sub-admin', username: 'studio-admin', is_admin: true };

function Probe() {
  const { completed, pending } = useXeltoSetup();
  return <p>{`completed:${String(completed)} pending:${String(pending)}`}</p>;
}

function renderAs(user: XeltoUser | null) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <XeltoUserContext.Provider value={user}>
        <Probe />
      </XeltoUserContext.Provider>
    </QueryClientProvider>,
  );
}

describe('useXeltoSetup', () => {
  beforeEach(() => {
    Object.assign(answers, { models_ready: true, prompted: true, failing: false });
    localStorage.clear();
  });

  it('leaves upstream alone with multi-user mode off', () => {
    renderAs(null);
    expect(screen.getByText('completed:null pending:false')).toBeInTheDocument();
  });

  it('skips the wizard for everyone once the server is set up', async () => {
    renderAs(ADMIN);
    expect(await screen.findByText('completed:true pending:false')).toBeInTheDocument();
  });

  it('gives the admin the wizard while the server is not set up', async () => {
    answers.models_ready = false;
    renderAs(ADMIN);
    expect(await screen.findByText('completed:false pending:false')).toBeInTheDocument();
  });

  it('has a user wait while the analytics question is open', async () => {
    answers.prompted = false;
    renderAs(USER);
    expect(await screen.findByText('completed:true pending:true')).toBeInTheDocument();
  });

  it('lets a user in once the server is set up', async () => {
    renderAs(USER);
    expect(await screen.findByText('completed:true pending:false')).toBeInTheDocument();
  });

  it('never traps anyone when the server cannot be asked', async () => {
    answers.failing = true;
    renderAs(USER);
    expect(await screen.findByText('completed:true pending:false')).toBeInTheDocument();
  });
});

describe('XeltoSetupPending', () => {
  it('explains the wait and offers to sign out', () => {
    render(<XeltoSetupPending />);
    expect(screen.getByRole('status')).toHaveTextContent('The server is being set up');
    expect(screen.getByRole('button', { name: /sign out/i })).toBeInTheDocument();
  });
});
