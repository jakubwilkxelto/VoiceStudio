import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { afterEach, expect, it, vi } from 'vitest';

const mock = vi.hoisted(() => ({
  api: vi.fn(),
  toast: { success: vi.fn(), warning: vi.fn(), error: vi.fn() },
}));
vi.mock('@/lib/api/client', () => ({ apiJson: mock.api, describeError: String }));
vi.mock('sonner', () => ({ toast: mock.toast }));
vi.mock('@/hooks/use-backend-status', () => ({ useBackendStatus: () => ({ stage: 'ready' }) }));
vi.mock('@tanstack/react-router', () => ({
  Link: ({ to, children }: { to: string; children: ReactNode }) => <a href={to}>{children}</a>,
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

import { EngineFamilyList } from './engine-family-list';
import { ModelStoreShownContext } from './model-store-context';

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const base = { role: 'TTS', size_gb: 1, installed: false, supported: true };

function serve() {
  mock.api.mockImplementation((path: string) => {
    if (path === '/models')
      return Promise.resolve({
        models: [
          {
            ...base,
            repo_id: 'k2-fsa/OmniVoice',
            label: 'OmniVoice weights',
            engines: ['omnivoice'],
            curated: true,
            size_gb: 2.4,
          },
          { ...base, repo_id: 'mlx/kokoro', label: 'Kokoro', engines: ['mlx-audio'] },
          {
            ...base,
            repo_id: 'mlx/csm',
            label: 'CSM',
            engines: ['mlx-audio'],
            installed: true,
          },
          { ...base, repo_id: 'owner/loose', label: 'Loose model' },
        ],
      });
    if (path === '/engines')
      return Promise.resolve({
        tts: {
          active: 'omnivoice',
          active_model: 'k2-fsa/OmniVoice',
          backends: [
            {
              id: 'voxcpm',
              display_name: 'VoxCPM',
              available: false,
              reason: 'Needs CUDA',
              setup_snippet: 'uv sync --extra voxcpm',
            },
            {
              id: 'mlx-audio',
              display_name: 'MLX-Audio',
              available: true,
              reason: null,
              effective_device: 'mps',
              curated_models: [
                { key: 'kokoro', label: 'Kokoro', repo_id: 'mlx/kokoro' },
                { key: 'csm', label: 'CSM', repo_id: 'mlx/csm' },
              ],
            },
            {
              id: 'omnivoice',
              display_name: 'OmniVoice',
              available: true,
              reason: null,
              effective_device: 'cpu',
            },
          ],
        },
      });
    if (path === '/models/install/status') return Promise.resolve({ jobs: [] });
    if (path === '/model/loaded') return Promise.resolve({ models: [], count: 0 });
    if (path === '/engines/select') return Promise.resolve({ active: 'mlx-audio' });
    return Promise.resolve({});
  });
}

function renderList() {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <EngineFamilyList family="tts" />
    </QueryClientProvider>,
  );
}

it('lists each engine once with install and select on the same row, active first', async () => {
  serve();
  renderList();
  const omnivoice = await screen.findByRole('group', { name: 'OmniVoice' });
  const headings = screen.getAllByRole('heading', { level: 3 }).map((node) => node.textContent);
  expect(headings).toEqual(['OmniVoice', 'MLX-Audio', 'VoxCPM', 'Loose model']);
  // One row: the engine's weights download and its selection state.
  await waitFor(() =>
    expect(
      within(omnivoice).getByRole('button', { name: 'modelMaintenance.download' }),
    ).toBeEnabled(),
  );
  // Selected once, as a badge: no second, disabled "Selected" button.
  expect(within(omnivoice).queryByRole('button', { name: 'modelSettings.selected' })).toBeNull();
  expect(screen.getAllByText('modelSettings.selected')).toHaveLength(1);
  fireEvent.click(within(omnivoice).getByRole('button', { name: 'modelMaintenance.download' }));
  expect(mock.api).toHaveBeenCalledWith(
    '/models/install',
    expect.objectContaining({ body: JSON.stringify({ repo_id: 'k2-fsa/OmniVoice' }) }),
  );
  // The unavailable engine keeps its reason on the row.
  expect(screen.getByText('Needs CUDA')).toBeInTheDocument();
  expect(screen.queryByText('Kokoro')).not.toBeInTheDocument();
});

it('expands a multi-model engine to install and select one of its models', async () => {
  serve();
  renderList();
  const toggle = await screen.findByRole('button', { name: 'MLX-Audio' });
  expect(toggle).toHaveAttribute('aria-expanded', 'false');
  expect(await screen.findByText('modelSettings.modelsInstalled')).toBeInTheDocument();
  fireEvent.click(toggle);
  expect(toggle).toHaveAttribute('aria-expanded', 'true');
  const csm = await screen.findByRole('group', { name: 'CSM' });
  fireEvent.click(within(csm).getByRole('button', { name: 'modelSettings.select' }));
  await waitFor(() =>
    expect(mock.api).toHaveBeenCalledWith(
      '/engines/select',
      expect.objectContaining({
        body: JSON.stringify({ family: 'tts', backend_id: 'mlx-audio', model_id: 'csm' }),
      }),
    ),
  );
  expect(
    within(screen.getByRole('group', { name: 'Kokoro' })).getByRole('button', {
      name: 'modelMaintenance.download',
    }),
  ).toBeInTheDocument();
});

it('searches hosted models and opens the engine that holds the match', async () => {
  serve();
  renderList();
  await screen.findByRole('heading', { name: 'OmniVoice' });
  fireEvent.change(screen.getByRole('searchbox', { name: 'preferences.search' }), {
    target: { value: 'csm' },
  });
  expect(screen.queryByRole('heading', { name: 'OmniVoice' })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'MLX-Audio' })).toHaveAttribute(
    'aria-expanded',
    'true',
  );
  expect(screen.getByRole('group', { name: 'CSM' })).toBeInTheDocument();
});

it('leaves install-only model rows to the first-run store shown above it', async () => {
  serve();
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ModelStoreShownContext.Provider value>
        <EngineFamilyList family="tts" />
      </ModelStoreShownContext.Provider>
    </QueryClientProvider>,
  );
  await screen.findByRole('group', { name: 'OmniVoice' });
  const headings = screen.getAllByRole('heading', { level: 3 }).map((node) => node.textContent);
  expect(headings).toEqual(['OmniVoice', 'MLX-Audio', 'VoxCPM']);
});

it('opens the active multi-model engine once its models arrive after the engine list', async () => {
  let release!: () => void;
  const models = new Promise<void>((resolve) => (release = resolve));
  serve();
  const fallback = mock.api.getMockImplementation()!;
  mock.api.mockImplementation(async (path: string, ...rest: unknown[]) => {
    if (path === '/engines') {
      const engines = (await fallback(path, ...rest)) as { tts: Record<string, unknown> };
      return { tts: { ...engines.tts, active: 'mlx-audio', active_model: 'csm' } };
    }
    if (path === '/models') await models;
    return fallback(path, ...rest);
  });
  renderList();
  await screen.findByRole('heading', { name: 'MLX-Audio' });
  release();
  await waitFor(() =>
    expect(screen.getByRole('button', { name: 'MLX-Audio' })).toHaveAttribute(
      'aria-expanded',
      'true',
    ),
  );
});

it('reports a failed model catalogue with a retry instead of hiding downloads', async () => {
  serve();
  const fallback = mock.api.getMockImplementation()!;
  mock.api.mockImplementation((path: string, ...rest: unknown[]) =>
    path === '/models' ? Promise.reject(new Error('catalogue down')) : fallback(path, ...rest),
  );
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <EngineFamilyList family="tts" />
    </QueryClientProvider>,
  );
  expect(await screen.findByText('Error: catalogue down')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'backend.retry' })).toBeInTheDocument();
});
