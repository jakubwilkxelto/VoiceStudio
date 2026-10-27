import { render } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import '@/i18n';

vi.mock('@/components/waveform-player', () => ({ WaveformPlayer: () => null }));
vi.mock('./story-preview', () => ({ previewStoryLine: vi.fn() }));
vi.mock('./story-stems', () => ({ StoryStems: () => null }));
vi.mock('@/components/pipeline-failure', () => ({ PipelineFailure: () => null }));
import { StoryEditor } from './story-editor';
import { blankLongformDraft } from './longform-session';

it('is a growing, non-shrinking column of the page scroller', () => {
  const draft = {
    ...blankLongformDraft(),
    lines: [{ id: 'only', text: 'A single sentence.', profileId: null }],
  };
  const { container } = render(
    <StoryEditor draft={draft} profiles={[]} disabled={false} onChange={() => {}} />,
  );
  const classes = [...container.querySelector('[data-slot="story-editor"]')!.classList];
  expect(classes).toContain('flex-1');
  expect(classes).not.toContain('min-h-0');
});
