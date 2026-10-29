// XELTO fork: hide administration from signed-in users without the admin role.
//
// An allow-list on purpose: a Settings page upstream adds later stays hidden
// from ordinary users until someone decides it is theirs. The backend enforces
// the same split (backend/xelto/policy.py); hiding only spares users 403s.
import { SETTINGS_ENTRIES } from '@/features/settings/settings-index';
import { useXeltoRestricted } from './session';
import { useXeltoThemeLocked } from './theme-lock';

export const USER_SETTINGS_PAGES: ReadonlySet<string> = new Set([
  '/settings',
  '/settings/general',
  '/settings/appearance',
  '/settings/pronunciation',
  '/settings/support',
]);

export function visibleToUser(to: string): boolean {
  return USER_SETTINGS_PAGES.has(to);
}

/** Settings → extra pages, without the ones a restricted user cannot use. */
export function useXeltoVisibleSettings<T extends { to: string }>(items: readonly T[]): T[] {
  const restricted = useXeltoRestricted();
  return restricted ? items.filter((item) => visibleToUser(item.to)) : [...items];
}

/** Settings → pinned pages (models, media tools, logs) are administration. */
export function useXeltoPinned<T extends Record<string, boolean>>(pinned: T): T {
  const restricted = useXeltoRestricted();
  if (!restricted) return pinned;
  return Object.fromEntries(Object.keys(pinned).map((key) => [key, false])) as T;
}

const settingsTargetByLabel = new Map(SETTINGS_ENTRIES.map((entry) => [entry.label, entry.to]));
// Command palette entries that open administration outside SETTINGS_ENTRIES.
const ADMIN_COMMANDS = new Set(['models']);

// The light/dark switch; the theme is locked in multi-user mode (theme-lock.tsx).
const THEME_COMMAND = 'theme';

/** Command palette items, without administration for a restricted user. */
export function useXeltoCommands<T extends { id: string }>(items: T[]): T[] {
  const restricted = useXeltoRestricted();
  const themeLocked = useXeltoThemeLocked();
  if (themeLocked) items = items.filter((item) => item.id !== THEME_COMMAND);
  if (!restricted) return items;
  return items.filter((item) => {
    if (ADMIN_COMMANDS.has(item.id)) return false;
    if (!item.id.startsWith('settings-')) return true;
    const to = settingsTargetByLabel.get(item.id.slice('settings-'.length));
    return to === undefined || visibleToUser(to);
  });
}
