import { BookOpen, CircleDot, Contrast, Monitor, Moon, MoonStar, Sun } from 'lucide-react';
import type { Theme } from '@/lib/theme';

/** The appearance modes, in the order the Appearance sheet offers them. Kept out of the component file so
 *  fast refresh can still swap the sheet on its own. */
export const MODES: { id: Theme; label: string; icon: typeof Sun }[] = [
  { id: 'light', label: 'Light', icon: Sun },
  { id: 'dark', label: 'Dark', icon: Moon },
  { id: 'dim', label: 'Dim', icon: MoonStar },
  { id: 'midnight', label: 'Midnight', icon: CircleDot },
  { id: 'sepia', label: 'Sepia', icon: BookOpen },
  { id: 'high-contrast', label: 'Contrast', icon: Contrast },
  { id: 'system', label: 'System', icon: Monitor },
];
