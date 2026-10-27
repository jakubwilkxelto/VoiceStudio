import { describe, expect, it } from 'vitest';
import { XELTO_LOCALES } from './i18n';

const upstreamLocales = Object.keys(import.meta.glob('../i18n/locales/*.json'))
  .map((path) => path.replace(/^.*\/([^/]+)\.json$/, '$1'))
  .sort();

const forkSources = import.meta.glob<string>(['./*.ts', './*.tsx', '!./*.test.ts', '!./*.test.tsx'], {
  query: '?raw',
  import: 'default',
  eager: true,
});

function keys(value: unknown, prefix = ''): string[] {
  return Object.entries(value as Record<string, unknown>).flatMap(([key, child]) =>
    typeof child === 'string' ? [`${prefix}${key}`] : keys(child, `${prefix}${key}.`),
  );
}

describe('fork locale catalogs', () => {
  it('cover every upstream locale', () => {
    expect(Object.keys(XELTO_LOCALES).sort()).toEqual(upstreamLocales);
  });

  it('have the same keys as English', () => {
    const english = keys(XELTO_LOCALES.en).sort();
    for (const [locale, catalog] of Object.entries(XELTO_LOCALES))
      expect(keys(catalog).sort(), locale).toEqual(english);
  });

  it('keep {{placeholders}} in every translation', () => {
    const english = XELTO_LOCALES.en as Record<string, Record<string, string>>;
    for (const [locale, catalog] of Object.entries(XELTO_LOCALES)) {
      const typed = catalog as Record<string, Record<string, string>>;
      for (const [group, entries] of Object.entries(english))
        for (const [key, text] of Object.entries(entries))
          for (const placeholder of text.match(/\{\{\w+\}\}/g) ?? [])
            expect(typed[group][key], `${locale}:${group}.${key}`).toContain(placeholder);
    }
  });

  it('define every key the fork source uses', () => {
    const english = new Set(keys(XELTO_LOCALES.en));
    const used = new Set<string>();
    for (const source of Object.values(forkSources))
      for (const match of source.matchAll(/\bxt\(\s*['"]([A-Za-z0-9_.]+)['"]/g)) used.add(match[1]);
    expect(used.size).toBeGreaterThan(0);
    for (const key of used) expect(english.has(key), key).toBe(true);
  });
});
