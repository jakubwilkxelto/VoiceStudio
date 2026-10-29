import { act, fireEvent, render, screen } from '@testing-library/react';
import type { ReactNode } from 'react';
import { beforeEach, describe, expect, it } from 'vitest';
import '@/i18n';
import i18n from '@/i18n';
import { ThemeToggle } from '@/components/app-shell/theme-toggle';
import { TooltipProvider } from '@/components/ui/tooltip';
import { AppearanceResetButton, AppearanceSettings } from '@/features/settings/settings-preferences';
import { updateTheme } from '@/hooks/use-theme';
import { THEME_STORAGE_KEY } from '@/lib/themes/theme-preferences';
import { XeltoUserContext, type XeltoUser } from './session';
import { LOCKED_THEME, XeltoThemeLock } from './theme-lock';

const base = { sub: 's', username: 'u', name: '', email: '', roles: [] };
const USER: XeltoUser = { ...base, is_admin: false };
const ADMIN: XeltoUser = { ...base, is_admin: true };
const OTHER = { mode: 'light', light: 'signal', dark: 'orchid' } as const;

function renderAs(user: XeltoUser | null, children: ReactNode) {
  return render(
    <XeltoUserContext.Provider value={user}>
      <TooltipProvider>
        {user && <XeltoThemeLock />}
        {children}
      </TooltipProvider>
    </XeltoUserContext.Provider>,
  );
}

function stored() {
  return JSON.parse(localStorage.getItem(THEME_STORAGE_KEY) ?? '{}');
}

const root = document.documentElement;

describe('theme lock', () => {
  beforeEach(() => {
    act(() => updateTheme(OTHER));
  });

  it('leaves the theme and its controls alone in single-user mode', () => {
    renderAs(null, [<ThemeToggle key="toggle" />, <AppearanceSettings key="appearance" />]);
    expect(stored()).toEqual(OTHER);
    expect(root.classList.contains('dark')).toBe(false);
    expect(screen.getByText(i18n.t('preferences.theme'))).toBeTruthy();
    expect(screen.getByRole('button', { name: i18n.t('app.switch_to_dark') })).toBeTruthy();
    expect(screen.getAllByText(i18n.t('themeAppearance.default'))).toHaveLength(2);
  });

  it.each([
    ['a user', USER],
    ['an admin', ADMIN],
  ])('forces the default dark theme for %s', (_, user) => {
    renderAs(user, null);
    expect(stored()).toEqual(LOCKED_THEME);
    expect(root.classList.contains('dark')).toBe(true);
    expect(root.dataset.themeId).toBeUndefined();
  });

  it('puts the theme back after a change from elsewhere', () => {
    renderAs(USER, <AppearanceResetButton />);
    fireEvent.click(screen.getByRole('button', { name: i18n.t('preferences.reset') }));
    expect(stored()).toEqual(LOCKED_THEME);
    act(() => updateTheme({ mode: 'light' }));
    expect(stored()).toEqual(LOCKED_THEME);
    expect(root.classList.contains('dark')).toBe(true);
  });

  it('hides the theme controls but keeps the rest of Appearance', () => {
    renderAs(USER, [<ThemeToggle key="toggle" />, <AppearanceSettings key="appearance" />]);
    expect(screen.queryByText(i18n.t('preferences.theme'))).toBeNull();
    expect(screen.queryByText(i18n.t('themeAppearance.default'))).toBeNull();
    expect(screen.queryByRole('button', { name: /switch to/i })).toBeNull();
    expect(screen.getByText(i18n.t('preferences.font'))).toBeTruthy();
    expect(screen.getByText(i18n.t('preferences.ui_scale'))).toBeTruthy();
  });
});
