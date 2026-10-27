import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { I18nextProvider } from 'react-i18next';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import i18n from '../i18n';
import en from '../i18n/locales/en.json';

const mocks = vi.hoisted(() => ({ list: vi.fn(), resume: vi.fn() }));
vi.mock('../api/engines', () => ({
  listEngines: vi.fn().mockResolvedValue({ tts: { active: 'x', backends: [] } }),
}));
vi.mock('../api/generate', () => ({ audioUrl: (file) => `/audio/${file}` }));
vi.mock('../api/audiobook', () => ({
  audiobookPlan: vi.fn(),
  audiobookGenerate: vi.fn(),
  audiobookUploadCover: vi.fn(),
  audiobookPreviewChapter: vi.fn(),
  audiobookImport: vi.fn(),
  audiobookListJobs: mocks.list,
  audiobookResume: mocks.resume,
}));

import AudiobookTab from '../pages/AudiobookTab';
import { useAppStore } from '../store';

function streamResponse() {
  let sent = false;
  return {
    body: {
      getReader: () => ({
        read: async () => {
          if (sent) return { done: true };
          sent = true;
          return {
            done: false,
            value: new TextEncoder().encode(
              'data: {"type":"started","chapters":1}\n\n' +
                'data: {"type":"chapter","index":0,"title":"Cached","cached":true,"duration_s":1}\n\n' +
                'data: {"type":"done","output":"resumed.m4b","cached_chapters":1}\n\n',
            ),
          };
        },
        cancel: vi.fn(),
      }),
    },
  };
}

describe('AudiobookTab recovery stream', () => {
  beforeEach(() => {
    mocks.list
      .mockReset()
      .mockResolvedValueOnce({
        jobs: [
          {
            job_id: 'recover-me',
            type: 'audiobook',
            status: 'cancelled',
            title: 'Saved book',
            total_chapters: 1,
            chapters_done: 1,
            created_at: 1,
          },
        ],
      })
      .mockResolvedValue({ jobs: [] });
    mocks.resume.mockReset().mockResolvedValue(streamResponse());
    useAppStore.getState().setScript('# Unrelated draft\nDo not attach me.');
    useAppStore.getState().setLastOutput('');
  });

  it('resumes with an abort signal and stores output without the open draft', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    client.setQueryData(['system-info'], { outputs_dir: '/output' });
    render(
      <QueryClientProvider client={client}>
        <I18nextProvider i18n={i18n}>
          <AudiobookTab profiles={[]} />
        </I18nextProvider>
      </QueryClientProvider>,
    );
    fireEvent.click(await screen.findByRole('button', { name: en.audiobook.resume }));
    await waitFor(() => expect(mocks.resume).toHaveBeenCalledOnce());
    expect(mocks.resume.mock.calls[0][0]).toBe('recover-me');
    expect(mocks.resume.mock.calls[0][1].signal).toBeInstanceOf(AbortSignal);
    await waitFor(() => expect(useAppStore.getState().lastOutput).toBe('resumed.m4b'));
    expect(useAppStore.getState().lastOutputScript).toBe('');
  });
});
