// XELTO fork: one look on a multi-user server — VoiceStudio's default palette, dark.
//
// Everyone signed in, admins included, gets the same theme as the Keycloak
// sign-in pages (deploy/keycloak/themes/voicestudio). The controls are hidden;
// the lock itself is <XeltoThemeLock />, which puts the theme back whatever
// changed it (Appearance → Reset, another tab, preferences stored earlier).
import { useLayoutEffect } from 'react';
import { useTheme } from '@/hooks/use-theme';
import type { ThemePreferences } from '@/lib/themes/theme-preferences';
import { useXeltoUser } from './session';

export const LOCKED_THEME: ThemePreferences = { mode: 'dark', light: 'default', dark: 'default' };

/** True in multi-user mode: theme controls are hidden. */
export function useXeltoThemeLocked(): boolean {
  return useXeltoUser() !== null;
}

/** Keeps the locked theme applied; rendered for a signed-in user. */
export function XeltoThemeLock() {
  const { mode, light, dark, updateTheme } = useTheme();
  const drifted =
    mode !== LOCKED_THEME.mode || light !== LOCKED_THEME.light || dark !== LOCKED_THEME.dark;
  useLayoutEffect(() => {
    if (drifted) updateTheme(LOCKED_THEME);
  }, [drifted, updateTheme]);
  return null;
}
