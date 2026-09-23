// Minimal 18px stroke icon set (currentColor) — keeps the UI crisp and
// dependency-free. All icons are simple geometric shapes.
type IconProps = { size?: number; className?: string };

function Svg({ size = 18, className, children }: IconProps & { children: React.ReactNode }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden="true">
      {children}
    </svg>
  );
}

export const IconDashboard = (p: IconProps) => (
  <Svg {...p}><rect x="3" y="3" width="7" height="9" rx="1.5" /><rect x="14" y="3" width="7" height="5" rx="1.5" /><rect x="14" y="12" width="7" height="9" rx="1.5" /><rect x="3" y="16" width="7" height="5" rx="1.5" /></Svg>
);
export const IconLive = (p: IconProps) => (
  <Svg {...p}><rect x="2" y="5" width="20" height="14" rx="3" /><path d="M10 9l5 3-5 3z" fill="currentColor" stroke="none" /></Svg>
);
export const IconDevices = (p: IconProps) => (
  <Svg {...p}><rect x="2" y="7" width="10" height="10" rx="2" /><rect x="14" y="9" width="8" height="6" rx="2" /><path d="M7 12h2M17 12h1" /></Svg>
);
export const IconInspections = (p: IconProps) => (
  <Svg {...p}><rect x="5" y="3" width="14" height="18" rx="2" /><path d="M9 8h6M9 12h6M9 16h4" /></Svg>
);
export const IconAnalytics = (p: IconProps) => (
  <Svg {...p}><path d="M4 20V10M10 20V4M16 20v-8M22 20H2" /></Svg>
);
export const IconAnomaly = (p: IconProps) => (
  <Svg {...p}><path d="M12 3L2 20h20z" /><path d="M12 10v4M12 17h.01" /></Svg>
);
export const IconWrench = (p: IconProps) => (
  <Svg {...p}><path d="M14.5 6.5a4 4 0 105.5 5.5L17 15l-2.5-2.5L12 15l-3-3-3 3" /></Svg>
);
export const IconReport = (p: IconProps) => (
  <Svg {...p}><path d="M7 3h7l4 4v14H7z" /><path d="M14 3v4h4M10 12h4M10 16h4" /></Svg>
);
export const IconUsers = (p: IconProps) => (
  <Svg {...p}><circle cx="9" cy="8" r="3.5" /><path d="M3 20c0-3.3 2.7-6 6-6s6 2.7 6 6" /><circle cx="17" cy="9" r="2.5" /><path d="M17 14c2.5.4 4 2.3 4 5" /></Svg>
);
export const IconSettings = (p: IconProps) => (
  <Svg {...p}><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 00.3 1.9l.1.1a2 2 0 11-2.8 2.8l-.1-.1a1.7 1.7 0 00-1.9-.3 1.7 1.7 0 00-1 1.5V21a2 2 0 11-4 0v-.1a1.7 1.7 0 00-1-1.6 1.7 1.7 0 00-1.9.3l-.1.1a2 2 0 11-2.8-2.8l.1-.1a1.7 1.7 0 00.3-1.9 1.7 1.7 0 00-1.5-1H3a2 2 0 110-4h.1a1.7 1.7 0 001.6-1 1.7 1.7 0 00-.3-1.9l-.1-.1a2 2 0 112.8-2.8l.1.1a1.7 1.7 0 001.9.3h.1a1.7 1.7 0 001-1.5V3a2 2 0 114 0v.1a1.7 1.7 0 001 1.6h.1a1.7 1.7 0 001.9-.3l.1-.1a2 2 0 112.8 2.8l-.1.1a1.7 1.7 0 00-.3 1.9v.1a1.7 1.7 0 001.5 1H21a2 2 0 110 4h-.1a1.7 1.7 0 00-1.5 1z" /></Svg>
);
export const IconBell = (p: IconProps) => (
  <Svg {...p}><path d="M18 9a6 6 0 10-12 0c0 5-2 6-2 6h16s-2-1-2-6" /><path d="M10 19a2 2 0 004 0" /></Svg>
);
export const IconSearch = (p: IconProps) => (
  <Svg {...p}><circle cx="11" cy="11" r="7" /><path d="M20 20l-3.5-3.5" /></Svg>
);
export const IconLogout = (p: IconProps) => (
  <Svg {...p}><path d="M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4" /><path d="M16 17l5-5-5-5M21 12H9" /></Svg>
);
export const IconMenu = (p: IconProps) => (
  <Svg {...p}><path d="M4 6h16M4 12h16M4 18h16" /></Svg>
);
export const IconChevron = (p: IconProps) => (
  <Svg {...p}><path d="M9 6l6 6-6 6" /></Svg>
);
export const IconPlus = (p: IconProps) => (
  <Svg {...p}><path d="M12 5v14M5 12h14" /></Svg>
);
export const IconWifi = (p: IconProps) => (
  <Svg {...p}><path d="M5 12.5a10 10 0 0114 0M8.5 15.5a5.5 5.5 0 017 0" /><path d="M12 19h.01" /></Svg>
);
