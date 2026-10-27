import { useTranslation } from 'react-i18next';

export interface RenderSummary {
  engine?: string;
  voices?: { id: string; name: string }[];
  language?: string;
  format?: string;
  lines?: number;
  words?: number;
  speeds?: number[];
  options?: Record<string, unknown>;
  chapter_titles?: string[];
}

export interface RenderRecord {
  job_id: string;
  title?: string;
  output: string;
  type?: 'story' | 'audiobook';
  created_at?: number | string;
  duration_s?: number;
  chapters?: number;
  summary?: RenderSummary;
}

const OPTION_LABELS: Record<string, string> = {
  num_step: 'clone.steps',
  guidance_scale: 'clone.cfg',
  position_temperature: 'clone.pos_temp',
  class_temperature: 'clone.class_temp',
  postprocess_output: 'clone.postprocess',
  seed: 'audiobook.seed',
  emo_vector: 'audiobook.emotion_help',
  emo_text: 'audiobook.emotion_text',
  emo_alpha: 'audiobook.emotion_alpha',
  vary_repeats: 'audiobook.vary_repeats',
  line_gap_ms: 'audiobook.line_gap',
  paragraph_gap_ms: 'audiobook.paragraph_gap',
  trim_edges: 'audiobook.trim_edges',
};

/** Seconds as `m:ss`, or `h:mm:ss` from one hour up. */
export function clock(seconds?: number): string {
  const total = Math.max(0, Math.round(Number(seconds) || 0));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = String(total % 60).padStart(2, '0');
  return h > 0 ? `${h}:${String(m).padStart(2, '0')}:${s}` : `${m}:${s}`;
}

/** `1.00×` for one speed, `0.90–1.25×` for a range, nothing without usable values. */
export function speedLabel(speeds: unknown): string {
  if (!Array.isArray(speeds)) return '';
  const finite = speeds.filter((v): v is number => typeof v === 'number' && Number.isFinite(v));
  if (!finite.length) return '';
  const low = Math.min(...finite).toFixed(2);
  const high = Math.max(...finite).toFixed(2);
  return low === high ? `${low}×` : `${low}–${high}×`;
}

/** Voice names (id when a name is blank) joined by commas. */
export function voiceLabel(summary?: RenderSummary): string {
  const voices: unknown = summary?.voices;
  if (!Array.isArray(voices)) return '';
  return voices
    .map((voice: { id?: string; name?: string } | null) =>
      voice && typeof voice === 'object' ? voice.name || voice.id || '' : '',
    )
    .filter(Boolean)
    .join(', ');
}

/** One-line recipe: voice · speed · engine · length. */
export function renderRecipe(render: RenderRecord): string {
  const summary = render.summary;
  return [
    voiceLabel(summary),
    speedLabel(summary?.speeds),
    summary?.engine,
    render.duration_s ? clock(render.duration_s) : '',
  ]
    .filter(Boolean)
    .join(' · ');
}

export function RenderDetails({ render }: { render: RenderRecord }) {
  const { t } = useTranslation();
  const summary = render.summary;
  if (!summary) {
    return <p className="mt-2 text-xs text-muted-foreground">{t('projects.render_no_details')}</p>;
  }
  const options =
    summary.options && typeof summary.options === 'object' && !Array.isArray(summary.options)
      ? summary.options
      : {};
  const settings = Object.entries(options)
    .filter(([key]) => Object.hasOwn(OPTION_LABELS, key))
    .map(([key, value]) => {
      const display =
        typeof value === 'boolean'
          ? t(value ? 'pronunciation.enabled' : 'supportPlans.disabled')
          : String(value);
      return `${t(OPTION_LABELS[key])} ${display}`;
    })
    .join(' · ');
  const rows: [string, string][] = [
    [t('projects.render_voice'), voiceLabel(summary)],
    [t('projects.render_speed'), speedLabel(summary.speeds)],
    [t('projects.render_engine'), summary.engine || ''],
    [t('projects.render_language'), summary.language || ''],
    [t('projects.render_format'), (summary.format || '').toUpperCase()],
    [
      t('projects.render_size_label'),
      t('projects.render_size', {
        chapters: render.chapters ?? summary.chapter_titles?.length ?? 0,
        lines: summary.lines ?? 0,
        words: summary.words ?? 0,
      }),
    ],
    [t('projects.render_settings'), settings],
  ];
  return (
    <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs">
      {rows
        .filter(([, value]) => value)
        .map(([label, value]) => (
          <div key={label} className="contents">
            <dt className="text-muted-foreground">{label}</dt>
            <dd className="min-w-0 break-words">{value}</dd>
          </div>
        ))}
    </dl>
  );
}

/** Collapsible "how it was made" block for a Projects row. */
export function RenderDisclosure({ render }: { render: RenderRecord }) {
  const { t } = useTranslation();
  return (
    <details className="group mt-1.5">
      <summary className="w-fit cursor-pointer select-none text-xs text-muted-foreground underline-offset-2 hover:underline">
        {t('projects.render_details')}
      </summary>
      <RenderDetails render={render} />
    </details>
  );
}
