// XELTO fork: strings for multi-user mode, in their own i18next namespace so the
// upstream catalog (i18n/locales) and its parity checks stay untouched.
import i18next from 'i18next';
import { useTranslation } from 'react-i18next';

export const XELTO_NS = 'xelto';

const catalogs = import.meta.glob<{ default: Record<string, unknown> }>('../i18n/xelto/*.json', {
  eager: true,
});

export const XELTO_LOCALES: Record<string, Record<string, unknown>> = Object.fromEntries(
  Object.entries(catalogs).map(([path, module]) => [
    path.replace(/^.*\/([^/]+)\.json$/, '$1'),
    module.default,
  ]),
);

function register() {
  for (const [locale, catalog] of Object.entries(XELTO_LOCALES)) {
    i18next.addResourceBundle(locale, XELTO_NS, catalog, true, true);
  }
}

// init() rebuilds the resource store, so bundles added before it would be lost.
if (i18next.isInitialized) register();
else i18next.on('initialized', register);

/**
 * Translate a fork string. Named `xt` rather than `t` on purpose: Electron's
 * locale:check resolves every `t('…')` key against the upstream catalog.
 */
export function useXt() {
  const { t } = useTranslation(XELTO_NS);
  return t;
}
