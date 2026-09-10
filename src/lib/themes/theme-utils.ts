// ──────────────────────────────────────────────────────
// Theme types
// ──────────────────────────────────────────────────────

export type Theme = "dark" | "light" | "system" | "dim" | "midnight" | "sepia" | "high-contrast";

export type ThemeColor = {
  name: string;
  value: string;
  lightVars: Record<string, string>;
  darkVars: Record<string, string>;
};

export type SurfaceToneOption = {
  value: string;
  label: string;
  description: string;
  mode: "light" | "dark";
  preview: string; // HSL for preview swatch
  overrides: Record<string, string>;
};

export type FontFamily = "inter" | "system" | "serif" | "mono" | "poppins" | "dm-sans";
export type FontSize = "compact" | "default" | "comfortable" | "large";

export type ThemeProviderProps = {
  children: React.ReactNode;
  defaultTheme?: Theme;
  defaultColor?: string;
  storageKey?: string;
};

export type ThemeProviderState = {
  theme: Theme;
  themeColor: string;
  surfaceTone: string;
  radius: number;
  fontFamily: FontFamily;
  fontSize: FontSize;
  setTheme: (theme: Theme) => void;
  setThemeColor: (color: string) => void;
  setSurfaceTone: (tone: string) => void;
  setRadius: (radius: number) => void;
  setFontFamily: (font: FontFamily) => void;
  setFontSize: (size: FontSize) => void;
  availableColors: ThemeColor[];
  surfaceTones: SurfaceToneOption[];
  fontFamilies: { value: FontFamily; label: string; preview: string }[];
  fontSizes: { value: FontSize; label: string; size: string }[];
};

// ──────────────────────────────────────────────────────
// makeTheme — generates a full ThemeColor from HSL params
// ──────────────────────────────────────────────────────

export function makeTheme(
  name: string,
  value: string,
  h: number,
  s: number,
  l: number,
  opts?: { darkL?: number; fgLight?: boolean }
): ThemeColor {
  const dl = opts?.darkL ?? Math.min(l + 10, 68);
  const fg = opts?.fgLight ?? (l > 65);
  const fgS = Math.min(s * 0.15, 20);
  const secS = Math.min(s * 0.4, 35);
  const mfS = Math.min(s * 0.1, 10);
  const brS = Math.min(s * 0.25, 25);
  const dbS = Math.min(s * 0.18, 18);
  const dfS = Math.min(s * 0.5, 60);
  const dcS = Math.min(s * 0.15, 16);
  const dsS = Math.min(s * 0.1, 12);
  const dmS = Math.min(s * 0.2, 20);
  const daS = Math.min(s * 0.12, 14);
  const ddS = Math.min(s * 0.1, 12);
  const pfg = fg ? `${h} ${Math.min(s * 0.2, 20)}% 10%` : "0 0% 100%";
  return {
    name,
    value,
    lightVars: {
      "--background": "0 0% 100%",
      "--foreground": `${h} ${fgS}% 10%`,
      "--card": "0 0% 100%",
      "--card-foreground": `${h} ${fgS}% 10%`,
      "--popover": "0 0% 100%",
      "--popover-foreground": `${h} ${fgS}% 10%`,
      "--primary": `${h} ${s}% ${l}%`,
      "--primary-foreground": pfg,
      "--secondary": `${h} ${secS}% 96%`,
      "--secondary-foreground": `${h} ${fgS}% 10%`,
      "--muted": `${h} ${secS}% 96%`,
      "--muted-foreground": `${h} ${mfS}% 45%`,
      "--accent": `${h} ${secS}% 96%`,
      "--accent-foreground": `${h} ${fgS}% 10%`,
      "--destructive": "0 84.2% 60.2%",
      "--destructive-foreground": "0 0% 98%",
      "--border": `${h} ${brS}% 91%`,
      "--input": `${h} ${brS}% 91%`,
      "--ring": `${h} ${s}% ${l}%`,
    },
    darkVars: {
      "--background": `${h} ${dbS}% 4%`,
      "--foreground": `${h} ${dfS}% 97%`,
      "--card": `${h} ${dcS}% 9%`,
      "--card-foreground": `${h} ${dfS}% 97%`,
      "--popover": `${h} ${dcS}% 7%`,
      "--popover-foreground": `${h} ${dfS}% 97%`,
      "--primary": `${h} ${s}% ${dl}%`,
      "--primary-foreground": pfg,
      "--secondary": `${h} ${dsS}% 14%`,
      "--secondary-foreground": `${h} ${dfS}% 97%`,
      "--muted": `${h} ${dsS}% 14%`,
      "--muted-foreground": `${h} ${dmS}% 65%`,
      "--accent": `${h} ${daS}% 17%`,
      "--accent-foreground": `${h} ${dfS}% 97%`,
      "--destructive": "0 62.8% 30.6%",
      "--destructive-foreground": "0 85.7% 97.3%",
      "--border": `${h} ${ddS}% 20%`,
      "--input": `${h} ${ddS}% 20%`,
      "--ring": `${h} ${s}% ${dl}%`,
    },
  };
}

// ──────────────────────────────────────────────────────
// Theme mode → light/dark base class mapping
// ──────────────────────────────────────────────────────

export const themeBaseMap: Record<string, "light" | "dark"> = {
  light: "light",
  dark: "dark",
  dim: "dark",
  midnight: "dark",
  sepia: "light",
  "high-contrast": "dark",
};

// ──────────────────────────────────────────────────────
// Mode-specific CSS overrides (applied AFTER color vars)
// ──────────────────────────────────────────────────────

export const themeModeOverrides: Record<string, Record<string, string>> = {
  dim: {
    "--background": "240 5% 12%",
    "--foreground": "0 0% 92%",
    "--card": "240 4% 16%",
    "--card-foreground": "0 0% 92%",
    "--popover": "240 4% 14%",
    "--popover-foreground": "0 0% 92%",
    "--secondary": "240 4% 20%",
    "--secondary-foreground": "0 0% 92%",
    "--muted": "240 4% 20%",
    "--muted-foreground": "240 5% 58%",
    "--accent": "240 4% 22%",
    "--accent-foreground": "0 0% 92%",
    "--border": "240 4% 24%",
    "--input": "240 4% 24%",
  },
  midnight: {
    "--background": "0 0% 0%",
    "--foreground": "0 0% 98%",
    "--card": "0 0% 3%",
    "--card-foreground": "0 0% 98%",
    "--popover": "0 0% 2%",
    "--popover-foreground": "0 0% 98%",
    "--secondary": "0 0% 8%",
    "--secondary-foreground": "0 0% 98%",
    "--muted": "0 0% 8%",
    "--muted-foreground": "0 0% 60%",
    "--accent": "0 0% 10%",
    "--accent-foreground": "0 0% 98%",
    "--border": "0 0% 12%",
    "--input": "0 0% 12%",
  },
  sepia: {
    "--background": "35 35% 94%",
    "--foreground": "25 25% 12%",
    "--card": "35 30% 96%",
    "--card-foreground": "25 25% 12%",
    "--popover": "35 30% 96%",
    "--popover-foreground": "25 25% 12%",
    "--secondary": "35 25% 88%",
    "--secondary-foreground": "25 20% 15%",
    "--muted": "35 25% 88%",
    "--muted-foreground": "25 12% 42%",
    "--accent": "35 22% 86%",
    "--accent-foreground": "25 20% 15%",
    "--border": "35 18% 82%",
    "--input": "35 18% 82%",
  },
  "high-contrast": {
    "--background": "0 0% 0%",
    "--foreground": "0 0% 100%",
    "--card": "0 0% 2%",
    "--card-foreground": "0 0% 100%",
    "--popover": "0 0% 2%",
    "--popover-foreground": "0 0% 100%",
    "--secondary": "0 0% 10%",
    "--secondary-foreground": "0 0% 100%",
    "--muted": "0 0% 10%",
    "--muted-foreground": "0 0% 75%",
    "--accent": "0 0% 12%",
    "--accent-foreground": "0 0% 100%",
    "--border": "0 0% 30%",
    "--input": "0 0% 30%",
  },
};

// ──────────────────────────────────────────────────────
// Font family CSS value mapping
// ──────────────────────────────────────────────────────

export const fontFamilyMap: Record<FontFamily, string> = {
  inter: "Inter, ui-sans-serif, system-ui, -apple-system, sans-serif",
  system: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
  serif: "Charter, 'Bitstream Charter', Georgia, 'Times New Roman', serif",
  mono: "ui-monospace, 'SF Mono', 'Cascadia Code', 'Fira Code', monospace",
  poppins: "Poppins, Inter, system-ui, sans-serif",
  "dm-sans": "'DM Sans', Inter, system-ui, sans-serif",
};

// ──────────────────────────────────────────────────────
// Font size CSS value mapping
// ──────────────────────────────────────────────────────

export const fontSizeMap: Record<FontSize, string> = {
  compact: "13px",
  default: "14px",
  comfortable: "15px",
  large: "16px",
};
