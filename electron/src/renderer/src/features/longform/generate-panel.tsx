import type { TFunction } from 'i18next';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { LongformActivityCard } from '@/components/longform-activity-card';
import { cn } from '@/lib/utils';
import { GenerationProgress } from './generation-progress';
import type { GenerateBlocker } from './generate-blocker';
import type { AudiobookRenderChapter } from './longform-session';
import type { RenderProgressSnapshot } from './render-eta';

export interface GenerateSession {
  active: 'stories' | 'audiobook' | null;
  stage: string;
  completed: number;
  total: number;
  failed: number;
  stopped: boolean;
  chapters: AudiobookRenderChapter[];
  progress?: RenderProgressSnapshot | null;
  startedAt?: number | null;
  lastActivityAt?: number | null;
}

const STATUS_ID = 'generate-status';

/** One line saying what the render is doing, or why it cannot start. */
function statusLine(
  t: TFunction,
  session: GenerateSession,
  mine: boolean,
  blocker: GenerateBlocker | null,
): string {
  if (!mine) return blocker ? t(`audiobook.blocked.${blocker}`) : '';
  switch (session.stage) {
    case 'assembling':
      return t('audiobook.assembling');
    case 'starting':
      return t('common.loading');
    default:
      return t('audiobook.progress_summary', {
        current: Math.min(session.completed + 1, session.total),
        total: session.total,
      });
  }
}

function Note({ children }: { children: string }) {
  return (
    <p role="status" aria-live="polite" className="shrink-0 text-xs text-muted-foreground">
      {children}
    </p>
  );
}

/** Shows the current long-form render state and its available action. */
export function GeneratePanel({
  mode,
  session,
  blocker,
  onGenerate,
  onStop,
}: {
  mode: 'stories' | 'audiobook';
  session: GenerateSession;
  blocker: GenerateBlocker | null;
  onGenerate: () => void;
  onStop: () => void;
}) {
  const { t } = useTranslation();
  const mine = session.active === mode;
  const status = statusLine(t, session, mine, blocker);

  return (
    <div data-slot="generate-panel" className="flex min-h-0 flex-col gap-2">
      {session.failed > 0 && <Note>{t('audiobook.failed_note', { count: session.failed })}</Note>}
      {session.stopped && !session.active && <Note>{t('audiobook.stopped_note')}</Note>}
      {mine && session.stage !== 'starting' && (
        <div className="max-h-[38vh] min-h-0 overflow-y-auto">
          <GenerationProgress
            chapters={session.chapters}
            assembling={session.stage === 'assembling'}
            stage={session.stage}
            progress={session.progress ?? null}
            startedAt={session.startedAt ?? null}
            lastActivityAt={session.lastActivityAt ?? null}
          />
        </div>
      )}
      {session.active && !mine && <LongformActivityCard className="shrink-0" />}
      {status && (
        <p
          id={STATUS_ID}
          role="status"
          className={cn(
            'text-xs text-muted-foreground',
            !session.active && blocker === 'engine' && 'sr-only',
          )}
        >
          {status}
        </p>
      )}
      {mine ? (
        <Button className="w-full" variant="outline" onClick={onStop}>
          {t('common.stop')}
        </Button>
      ) : (
        <Button
          className="w-full"
          variant="default"
          disabled={blocker !== null}
          aria-describedby={status ? STATUS_ID : undefined}
          onClick={onGenerate}
        >
          {t(mode === 'stories' ? 'stories.generateAll' : 'audiobook.create')}
        </Button>
      )}
    </div>
  );
}
