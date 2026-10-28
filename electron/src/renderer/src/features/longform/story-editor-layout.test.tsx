import '@/i18n';
import { render } from '@testing-library/react';
import { expect, it, vi } from 'vitest';

vi.mock('@/components/waveform-player', () => ({ WaveformPlayer: () => null }));
vi.mock('./story-preview', () => ({ previewStoryLine: vi.fn() }));
vi.mock('./story-stems', () => ({ StoryStems: () => null }));
vi.mock('@/components/pipeline-failure', () => ({ PipelineFailure: () => null }));

import { StoryEditor } from './story-editor';
import { blankLongformDraft } from './longform-session';

it('lets the story editor grow within the page scroller', () => {
  const draft = {
    ...blankLongformDraft(),
    lines: [{ id: 'opening', text: 'A quiet first line.', profileId: null }],
  };
  const { container } = render(
    <StoryEditor draft={draft} profiles={[]} disabled={false} onChange={() => {}} />,
  );
  const editor = container.querySelector('[data-slot="story-editor"]');
  expect(editor).toHaveClass('flex-1');
  expect(editor).not.toHaveClass('min-h-0');
});
