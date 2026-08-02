import {
  Activity,
  BarChart3,
  BrainCircuit,
  CandlestickChart,
  DatabaseZap,
  FlaskConical,
  ShieldCheck,
} from "lucide-react";
import { NavLink, Outlet } from "react-router-dom";

const navigation = [
  { to: "/", label: "每日候选", icon: CandlestickChart },
  { to: "/research-engines", label: "研究引擎", icon: DatabaseZap },
  { to: "/market-data", label: "行情数据", icon: DatabaseZap },
  { to: "/factor-research", label: "因子研究", icon: FlaskConical },
  { to: "/backtests", label: "回测实验", icon: BarChart3 },
  { to: "/ml-experiments", label: "模型实验", icon: BrainCircuit },
  { to: "/walk-forward-experiments", label: "Walk-forward", icon: Activity },
];

export function Layout() {
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">AS</div>
          <div>
            <strong>A-Share</strong>
            <span>Selection Lab</span>
          </div>
        </div>
        <nav aria-label="主导航">
          {navigation.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              end={to === "/"}
              className={({ isActive }) => (isActive ? "active" : undefined)}
            >
              <Icon size={18} />
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-safety">
          <ShieldCheck size={18} />
          <div>
            <strong>研究系统</strong>
            <span>不自动下单</span>
          </div>
        </div>
      </aside>
      <main>
        <header className="topbar">
          <div>
            <span className="eyebrow">A股每日选股、因子研究与自动复盘系统</span>
          </div>
          <div className="system-state">
            <Activity size={16} />
            <span>透明规则模型</span>
          </div>
        </header>
        <div className="page-container">
          <Outlet />
        </div>
        <footer>
          <span>不预测“明天必涨停” · 不使用虚假逐笔或 Level-2 数据</span>
          <span>研究结果不构成投资建议</span>
        </footer>
      </main>
    </div>
  );
}
