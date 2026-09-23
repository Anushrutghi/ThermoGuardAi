import { useCallback, useEffect, useRef, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { api, setToken, type Alarm, type Device } from "../api/client";
import {
  IconAnalytics,
  IconAnomaly,
  IconBell,
  IconDashboard,
  IconDevices,
  IconInspections,
  IconLive,
  IconLogout,
  IconMenu,
  IconReport,
  IconSettings,
  IconUsers,
  IconWrench,
} from "./icons";
import { firebaseConfigured, firebaseSignOut } from "../firebase";
import { cls, useToast } from "./ui";

interface SessionUser {
  username?: string;
  role?: string;
  full_name?: string;
  email?: string;
  organization?: string | null;
}

const NAV = [
  { to: "/", label: "Dashboard", icon: IconDashboard, roles: null },
  { to: "/live", label: "Live Inspection", icon: IconLive, roles: ["admin", "technician"] },
  { to: "/devices", label: "Devices", icon: IconDevices, roles: null },
  { to: "/inspections", label: "Inspections", icon: IconInspections, roles: null },
  { to: "/analytics", label: "Thermal Analytics", icon: IconAnalytics, roles: null },
  { to: "/anomalies", label: "Anomalies", icon: IconAnomaly, roles: null },
  { to: "/maintenance", label: "Maintenance", icon: IconWrench, roles: null },
  { to: "/reports", label: "Reports", icon: IconReport, roles: null },
  { to: "/team", label: "Team", icon: IconUsers, roles: ["admin"] },
  { to: "/settings", label: "Settings", icon: IconSettings, roles: null },
];

const PAGE_TITLES: Record<string, string> = {
  "/": "Dashboard",
  "/live": "Live Inspection",
  "/devices": "Devices",
  "/inspections": "Inspections",
  "/analytics": "Thermal Analytics",
  "/anomalies": "Anomaly Center",
  "/maintenance": "Maintenance",
  "/reports": "Reports",
  "/team": "Team",
  "/settings": "Settings",
};

function readUser(): SessionUser | null {
  try {
    return JSON.parse(sessionStorage.getItem("tg_user") ?? "null") as SessionUser | null;
  } catch {
    return null;
  }
}

export default function Layout() {
  const navigate = useNavigate();
  const location = useLocation();
  const toast = useToast();
  const [user, setUser] = useState<SessionUser | null>(readUser());
  const [collapsed, setCollapsed] = useState(false);
  const [drawer, setDrawer] = useState(false);
  const [notifOpen, setNotifOpen] = useState(false);
  const [profileOpen, setProfileOpen] = useState(false);
  const [conn, setConn] = useState<"connecting" | "on" | "off">("connecting");
  const [alarms, setAlarms] = useState<Alarm[]>([]);
  const [attentionDevices, setAttentionDevices] = useState<Device[]>([]);
  const [maintenanceCount, setMaintenanceCount] = useState(0);
  const notifRef = useRef<HTMLDivElement>(null);
  const profileRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const user = readUser();
    setUser(user);
    if (!user) navigate("/login");
  }, [location.pathname]); // eslint-disable-line react-hooks/exhaustive-deps

  // Connection health: poll the unauthenticated /health endpoint.
  useEffect(() => {
    let live = true;
    const poll = async () => {
      try {
        const r = await fetch("/health");
        const body = await r.json();
        if (live) setConn(body.status === "ok" ? "on" : "off");
      } catch {
        if (live) setConn("off");
      }
    };
    poll();
    const id = window.setInterval(poll, 30000);
    return () => {
      live = false;
      window.clearInterval(id);
    };
  }, []);

  // Real notifications: open alarms, devices needing attention, maintenance count.
  const loadNotifications = useCallback(() => {
    Promise.all([
      api.listAlarms(true).catch(() => [] as Alarm[]),
      api.listDevices().catch(() => [] as Device[]),
      api.dashboard().catch(() => null),
    ]).then(([al, devs, dash]) => {
      setAlarms(al);
      setAttentionDevices(devs.filter((d) => d.derived_status === "HIGH_RISK" || d.derived_status === "CRITICAL"));
      setMaintenanceCount(dash?.devices_requiring_maintenance ?? 0);
    });
  }, []);
  useEffect(() => {
    loadNotifications();
  }, [loadNotifications]);

  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      const t = e.target as Node;
      const outsideNotif = !notifRef.current || !notifRef.current.contains(t);
      const outsideProfile = !profileRef.current || !profileRef.current.contains(t);
      if (outsideNotif) setNotifOpen(false);
      if (outsideProfile) setProfileOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, []);

  useEffect(() => {
    setDrawer(false);
    setNotifOpen(false);
  }, [location.pathname]);

  const signOut = () => {
    setToken(null);
    sessionStorage.removeItem("tg_user");
    // Also clear the Firebase Auth session when the app uses Firebase.
    if (firebaseConfigured) void firebaseSignOut();
    toast("info", "Signed out");
    navigate("/login");
  };

  const notifCount = alarms.length + attentionDevices.length + (maintenanceCount > 0 ? 1 : 0);
  const navItems = NAV.filter((n) => !n.roles || (user && n.roles.includes(user.role ?? "")));
  const title = PAGE_TITLES[location.pathname] ?? (location.pathname.startsWith("/devices/") ? "Device" : "ThermoGuard");

  const sidebarContent = (mobile: boolean) => (
    <aside className={cls("sidebar", mobile && "sidebar-mobile")}>
      <div className="brand">
        <span className="brand-mark">⚡</span>
        <div style={{ minWidth: 0 }}>
          <span className="brand-name truncate">ThermoGuard AI</span>
          <span className="brand-sub">Thermal Inspection</span>
        </div>
      </div>
      <nav aria-label="Main navigation">
        <div className="nav-section">Operations</div>
        {navItems.map((n) => (
          <NavLink key={n.to} to={n.to} end={n.to === "/"} className={({ isActive }) => cls("nav-item", isActive && "active")}>
            <n.icon size={17} className="nav-ico" />
            <span>{n.label}</span>
          </NavLink>
        ))}
      </nav>
      <div className="sidebar-footer">
        <div className="user-chip">
          <span className="avatar">{(user?.username ?? "?")[0]?.toUpperCase()}</span>
          <div style={{ minWidth: 0 }}>
            <strong className="truncate">{user?.full_name || user?.username || "Guest"}</strong>
            <small>
              {user?.role ?? ""}
              {user?.organization ? ` · ${user.organization}` : ""}
            </small>
          </div>
        </div>
        <ButtonGhost onClick={signOut} label="Sign out" />
      </div>
    </aside>
  );

  return (
    <div className={cls("shell", collapsed && "collapsed")}>
      {sidebarContent(false)}

      {/* mobile drawer */}
      {drawer && <div className="drawer-backdrop" onClick={() => setDrawer(false)} />}
      {drawer && (
        <div className="drawer">
          <div className="brand">
            <span className="brand-mark">⚡</span>
            <div>
              <span className="brand-name">ThermoGuard AI</span>
              <span className="brand-sub">Thermal Inspection</span>
            </div>
          </div>
          <nav aria-label="Mobile navigation">
            {navItems.map((n) => (
              <NavLink key={n.to} to={n.to} end={n.to === "/"} className={({ isActive }) => cls("nav-item", isActive && "active")}>
                <n.icon size={17} className="nav-ico" />
                <span>{n.label}</span>
              </NavLink>
            ))}
          </nav>
          <div style={{ marginTop: 16 }}>
            <ButtonGhost onClick={signOut} label="Sign out" />
          </div>
        </div>
      )}

      <div className="content">
        <header className="topbar">
          <button className="icon-btn menu-btn" aria-label="Open navigation" onClick={() => setDrawer(true)}>
            <IconMenu />
          </button>
          <button className="icon-btn" aria-label="Collapse sidebar" onClick={() => setCollapsed((v) => !v)} style={{ display: "grid" }}>
            <IconMenu />
          </button>
          <div className="topbar-title">{title}</div>
          <div className="topbar-spacer" />
          <div className="searchbox" style={{ width: 240 }}>
            <input
              ref={searchRef}
              placeholder="Search inspections…"
              aria-label="Search inspections"
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  navigate(`/inspections${searchRef.current?.value ? `?q=${encodeURIComponent(searchRef.current.value)}` : ""}`);
                }
              }}
            />
          </div>
          <div className="org-chip">
            <span className="dot" />
            {user?.organization ?? "No organization"}
          </div>
          <span className={cls("conn-pill", conn)} title="Backend connection status">
            <span className="dot" />
            {conn === "on" ? "Connected" : conn === "connecting" ? "Connecting" : "Offline"}
          </span>
          <div className="popover-wrap" ref={notifRef}>
            <button className="icon-btn" aria-label="Notifications" onClick={() => setNotifOpen((v) => !v)}>
              <IconBell />
              {notifCount > 0 && <span className="ping" />}
            </button>
            {notifOpen && (
              <div className="popover card card-tight">
                <div className="page-head" style={{ marginBottom: 8 }}>
                  <h3 style={{ marginBottom: 0 }}>Notifications</h3>
                  <button className="btn ghost small" onClick={loadNotifications}>
                    Refresh
                  </button>
                </div>
                {notifCount === 0 && <div className="state" style={{ padding: "18px 8px" }}><div className="s-ico">✓</div><div className="s-body">All clear — no open alerts.</div></div>}
                <div className="notif-list">
                  {attentionDevices.map((d) => (
                    <div key={`d${d.id}`} className="notif-item alert" onClick={() => { setNotifOpen(false); navigate(`/devices/${d.id}`); }} style={{ cursor: "pointer" }}>
                      <span className="n-ico">⚠</span>
                      <div>
                        <strong>{d.name}</strong>
                        <span>{d.derived_status} risk status — open device</span>
                        <small>{d.location ?? "No location"}</small>
                      </div>
                    </div>
                  ))}
                  {maintenanceCount > 0 && (
                    <div className="notif-item">
                      <span className="n-ico">🔧</span>
                      <div>
                        <strong>{maintenanceCount} device{maintenanceCount === 1 ? "" : "s"} require maintenance</strong>
                        <small>From the maintenance center</small>
                      </div>
                    </div>
                  )}
                  {alarms.map((a) => (
                    <div key={`a${a.id}`} className={cls("notif-item", a.severity === "critical" && "alert")}>
                      <span className="n-ico">{a.severity === "critical" ? "🚨" : "⚠"}</span>
                      <div>
                        <strong>{a.severity.toUpperCase()}</strong>
                        <span>{a.message}</span>
                        <small>{new Date(a.created_at).toLocaleString()}</small>
                      </div>
                    </div>
                  ))}
                </div>
                <p className="chart-legend-note" style={{ margin: "8px 0 0" }}>
                  Real system events only. Persistent push notifications are not yet supported by the backend.
                </p>
              </div>
            )}
          </div>
          <div className="popover-wrap" ref={profileRef}>
            <button className="icon-btn" aria-label="User profile" onClick={() => setProfileOpen((v) => !v)} style={{ borderRadius: "50%" }}>
              {(user?.username ?? "?")[0]?.toUpperCase()}
            </button>
            {profileOpen && (
              <div className="popover card card-tight">
                <div style={{ padding: "4px 6px 10px" }}>
                  <strong>{user?.full_name || user?.username}</strong>
                  <div className="muted" style={{ fontSize: 13 }}>{user?.email ?? ""}</div>
                  <div className="flex" style={{ marginTop: 8 }}>
                    <span className="chip info">{user?.role}</span>
                    <span className="chip plain">{user?.organization ?? "No org"}</span>
                  </div>
                </div>
                <button className="btn ghost block small" onClick={() => { setProfileOpen(false); navigate("/settings"); }}>
                  Settings
                </button>
                <button className="btn danger block small" onClick={signOut} style={{ marginTop: 8 }}>
                  <IconLogout size={14} /> Sign out
                </button>
              </div>
            )}
          </div>
        </header>
        <main className="content-inner">
          <Outlet />
        </main>
      </div>
    </div>
  );
}

function ButtonGhost({ onClick, label }: { onClick: () => void; label: string }) {
  return (
    <button className="btn ghost block small" onClick={onClick}>
      <IconLogout size={14} /> {label}
    </button>
  );
}
