import { MoonIcon, SunIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { useTheme } from '@/hooks/use-theme';
import { useXeltoThemeLocked } from '@/xelto/theme-lock'; // XELTO: one theme in multi-user mode

export function ThemeToggle() {
  const { t } = useTranslation();
  const { theme, setTheme } = useTheme();
  if (useXeltoThemeLocked()) return null; // XELTO: one theme in multi-user mode
  const next = theme === 'dark' ? 'light' : 'dark';
  const label = next === 'light' ? t('app.switch_to_light') : t('app.switch_to_dark');
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <Button
            variant="ghost"
            size="icon-sm"
            className="app-no-drag"
            aria-label={label}
            onClick={() => setTheme(next)}
          />
        }
      >
        {theme === 'dark' ? <SunIcon /> : <MoonIcon />}
      </TooltipTrigger>
      <TooltipContent side="top">{label}</TooltipContent>
    </Tooltip>
  );
}
