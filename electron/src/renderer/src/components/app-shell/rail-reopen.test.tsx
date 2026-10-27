import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

// Real hook and store here; only the router, bridge and heavy children are replaced.
vi.mock('@tanstack/react-router', () => ({
  Link: ({ to, children, ...rest }: any) => (
    <a href={to} {...rest}>
      {children}
    </a>
  ),
  useRouterState: ({ select }: any) => select({ location: { pathname: '/clone' } }),
}));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('@/components/bridge', () => ({ isMac: () => false, appVersion: () => '1.0.0' }));
vi.mock('@/hooks/use-window-fullscreen', () => ({ useWindowFullscreen: () => false }));
vi.mock('@/hooks/use-pane-resize', () => ({
  usePaneResize: () => ({ host: { current: null }, width: 200, separatorProps: {} }),
}));
vi.mock('./workspace-menu', () => ({ WorkspaceNavigation: () => null }));
vi.mock('./status-bar', () => ({ StatusBar: () => null }));
import { setWorkspace } from '@/lib/store/workspace';
import { WorkspaceSidebar } from './workspace-sidebar';

beforeEach(() => {
  vi.stubGlobal('matchMedia', () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
  }));
  setWorkspace({ libraryOpen: true, expandedLibraryContext: null, autoCollapseSidebar: true });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it('the rail toggle swaps in the full sidebar and stores where it was asked for', () => {
  render(<WorkspaceSidebar />);
  fireEvent.click(screen.getByRole('button', { name: 'clone.toggle_sidebar' }));
  expect(screen.queryByRole('button', { name: 'clone.toggle_sidebar' })).toBeNull();
  expect(screen.getByRole('button', { name: 'common.close' })).toBeTruthy();
  const stored = JSON.parse(localStorage.getItem('voicestudio.workspace-layout')!);
  expect(stored.libraryOpen).toBe(true);
  expect(stored.expandedLibraryContext).toBe('/clone:false');
});
