// XELTO fork: who is signed in, and Sign out, in the window's top-right corner.
import { Menu } from '@base-ui/react/menu';
import { LogOutIcon, ShieldCheckIcon, UserRoundIcon } from 'lucide-react';
import { apiJson } from '@/lib/api/client';
import { runRendererTask } from '@/lib/global-error-recovery';
import { cn } from '@/lib/utils';
import { useXt } from './i18n';
import { LOGOUT_PATH, useXeltoUser } from './session';

const ITEM =
  'flex cursor-default items-center gap-2 rounded-md px-3 py-2 text-sm outline-none data-highlighted:bg-accent';

export async function signOut(assign: (url: string) => void = (url) => window.location.assign(url)) {
  try {
    const { logout_url } = await apiJson<{ logout_url: string }>(LOGOUT_PATH, { method: 'POST' });
    assign(logout_url || '/');
  } catch {
    assign('/');
  }
}

export function XeltoUserMenu({ className }: { className?: string }) {
  const xt = useXt();
  const user = useXeltoUser();
  if (!user) return null;
  const name = user.name || user.username || user.email;
  return (
    <Menu.Root>
      <Menu.Trigger
        aria-label={name || xt('menu.account')}
        data-slot="xelto-user-menu"
        className={cn(
          'flex h-8 max-w-56 min-w-0 items-center gap-2 rounded-full border border-border/60 bg-background/80 py-0.5 pr-3 pl-0.5 shadow-xs outline-none backdrop-blur-xl hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring',
          className,
        )}
      >
        <span className="grid size-7 shrink-0 place-items-center rounded-full bg-primary/15 text-primary">
          <UserRoundIcon className="size-4" aria-hidden="true" />
        </span>
        <span className="min-w-0 truncate text-sm font-medium">{name}</span>
        {user.is_admin && (
          <span className="rounded-full bg-primary/15 px-1.5 text-[11px] font-semibold text-primary">
            {xt('menu.admin_badge')}
          </span>
        )}
      </Menu.Trigger>
      <Menu.Portal>
        <Menu.Positioner sideOffset={6} align="end" className="z-50">
          <Menu.Popup className="min-w-52 rounded-lg border border-border bg-popover p-1 text-popover-foreground shadow-lg outline-none">
            <div className="px-3 py-2 text-xs text-muted-foreground">
              {xt('menu.signed_in_as', { name: user.username || name })}
            </div>
            {user.is_admin && (
              <div className="flex items-center gap-2 px-3 pb-2 text-xs text-muted-foreground">
                <ShieldCheckIcon className="size-3.5" aria-hidden="true" />
                {xt('menu.admin_badge')}
              </div>
            )}
            <Menu.Item
              className={ITEM}
              onClick={() => runRendererTask('Sign out', () => signOut())}
            >
              <LogOutIcon className="size-4" aria-hidden="true" />
              {xt('menu.sign_out')}
            </Menu.Item>
          </Menu.Popup>
        </Menu.Positioner>
      </Menu.Portal>
    </Menu.Root>
  );
}
