import { renderHook } from '@testing-library/react';
import type { ReactNode } from 'react';
import { describe, expect, it } from 'vitest';
import { SETTINGS_ENTRIES } from '@/features/settings/settings-index';
import { extraSettingsFor } from '@/features/settings/settings-page';
import {
  USER_SETTINGS_PAGES,
  useXeltoCommands,
  useXeltoPinned,
  useXeltoVisibleSettings,
} from './admin-visibility';
import { XeltoUserContext, type XeltoUser } from './session';

const base = { sub: 's', username: 'u', name: '', email: '', roles: [] };
const USER: XeltoUser = { ...base, is_admin: false };
const ADMIN: XeltoUser = { ...base, is_admin: true };

function as(user: XeltoUser | null) {
  return ({ children }: { children: ReactNode }) => (
    <XeltoUserContext.Provider value={user}>{children}</XeltoUserContext.Provider>
  );
}

const extras = extraSettingsFor(true);

describe('admin visibility', () => {
  it.each([
    ['single-user mode', null],
    ['an admin', ADMIN],
  ])('changes nothing for %s', (_, user) => {
    const pages = renderHook(() => useXeltoVisibleSettings(extras), { wrapper: as(user) });
    expect(pages.result.current).toEqual([...extras]);
    const pinned = renderHook(() => useXeltoPinned({ models: true, logs: true }), {
      wrapper: as(user),
    });
    expect(pinned.result.current).toEqual({ models: true, logs: true });
  });

  it('shows a user only the allow-listed settings pages', () => {
    const { result } = renderHook(() => useXeltoVisibleSettings(extras), { wrapper: as(USER) });
    const shown = result.current.map((item) => item.to);
    expect(shown.length).toBeGreaterThan(0);
    for (const to of shown) expect(USER_SETTINGS_PAGES.has(to)).toBe(true);
    for (const hidden of ['/settings/credentials', '/settings/storage', '/settings/network'])
      expect(shown).not.toContain(hidden);
  });

  it('hides pinned administration pages from a user', () => {
    const { result } = renderHook(() => useXeltoPinned({ models: true, media: true, logs: false }), {
      wrapper: as(USER),
    });
    expect(result.current).toEqual({ models: false, media: false, logs: false });
  });

  it('drops administration from the command palette for a user', () => {
    const items = [
      { id: 'clone' },
      { id: 'models' },
      ...SETTINGS_ENTRIES.map((entry) => ({ id: `settings-${entry.label}` })),
    ];
    const { result } = renderHook(() => useXeltoCommands(items), { wrapper: as(USER) });
    const ids = result.current.map((item) => item.id);
    expect(ids).toContain('clone');
    expect(ids).not.toContain('models');
    expect(ids).toContain('settings-preferences.theme');
    expect(ids).not.toContain('settings-settings.credentials');
    expect(ids).not.toContain('settings-settings.logs');
  });

  it.each([
    ['a user', USER],
    ['an admin', ADMIN],
  ])('drops the theme switch from the command palette for %s', (_, user) => {
    const items = [{ id: 'clone' }, { id: 'theme' }, { id: 'settings-preferences.theme' }];
    const { result } = renderHook(() => useXeltoCommands(items), { wrapper: as(user) });
    expect(result.current.map((item) => item.id)).toEqual(['clone', 'settings-preferences.theme']);
  });

  it('keeps the theme switch in single-user mode', () => {
    const items = [{ id: 'theme' }];
    const { result } = renderHook(() => useXeltoCommands(items), { wrapper: as(null) });
    expect(result.current).toEqual(items);
  });

  it('allow-list names pages that still exist', () => {
    const known = new Set([
      '/settings',
      '/settings/general',
      '/settings/appearance',
      ...extras.map((item) => item.to),
    ]);
    for (const to of USER_SETTINGS_PAGES) expect(known.has(to)).toBe(true);
  });
});
